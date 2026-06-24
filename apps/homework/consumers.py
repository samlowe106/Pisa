"""WebSocket consumer bridging the browser editor to a `lean --server` LSP process.

The browser speaks plain JSON-RPC (no LSP framing) and only knows about the student's editable
block. This consumer:

* authenticates the connection and loads the problem's fixed prefix/suffix (imported source files
  + fixed code) so the editable text can be spliced into a full, compilable Lean document;
* adds/strips the LSP ``Content-Length`` framing in each direction;
* rewrites ``didOpen``/``didChange`` to carry the assembled document, offsets ``$/lean/plainGoal``
  positions, and remaps ``publishDiagnostics`` line numbers back to the editor's coordinates.

See homework/lean_lsp.py for the framing/assembly/remapping helpers.
"""

import asyncio
import json
import shlex
import shutil
import tempfile
from pathlib import Path

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

from . import lean_lsp, sandbox
from .models import Problem, ProblemBlock

# At most one live `lean --server` process per user, across all their tabs/windows/devices.
# We track the current holder's channel name per user id. This is in-memory, which is correct
# for a single ASGI process (the project's default); a multi-process deployment would need a
# shared store (e.g. the cache or Redis) instead. A second, passive connection is rejected as
# "busy"; an explicit `?takeover=1` connection — the editor's "Use Lean here" button — evicts
# the current holder instead. Authentication is required to reach the editor at all, so keying
# on user id means an abuser needs many accounts, not just many tabs.
_LEAN_HOLDERS: dict[int, str] = {}

WS_CLOSE_BUSY = 4409
WS_CLOSE_TAKEN_OVER = 4410


def _user_group(user_id: int) -> str:
    return f"lean-user-{user_id}"


class LeanLSPConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)  # unauthenticated
            return

        self.user_id = user.id
        self.user_group = _user_group(self.user_id)
        self.has_claim = False
        takeover = b"takeover=1" in self.scope.get("query_string", b"")

        # One live Lean instance per user. A passive connection while another is already live is
        # rejected as busy; the client greys the editor and offers "Use Lean here", which
        # reconnects with ?takeover=1 to evict the current holder (handled before spawning).
        if not takeover and self.user_id in _LEAN_HOLDERS:
            await self.accept()
            await self._send_status(
                "busy", "A Pisa Lean instance is open in another window."
            )
            await self.close(code=WS_CLOSE_BUSY)
            return
        self._takeover = takeover

        self.problem_pk = self.scope["url_route"]["kwargs"]["problem_pk"]
        context = await self._load_context(self.problem_pk, user)
        if context is None:
            await self.close(code=4403)  # not allowed / no such problem
            return
        if context.get("unsupported"):
            await self.accept()
            await self._send_status(
                "unsupported",
                "Live feedback supports problems with a single editable block.",
            )
            await self.close(code=4002)
            return

        self.prefix = context["prefix"]
        self.suffix = context["suffix"]
        self.prefix_lines = self.prefix.count("\n")
        self.editable_lines = 1
        self.tmpdir = tempfile.mkdtemp(prefix="pisa_lsp_")
        self.client_uri = Path(
            self.tmpdir, f"pisa_problem_{self.problem_pk}.lean"
        ).as_uri()

        cmd = getattr(settings, "LEAN_LSP_CMD", None)
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        if not cmd:
            await self.accept()
            await self._send_status(
                "error", "LEAN_LSP_CMD is not configured on the server."
            )
            await self.close(code=4003)
            return

        # Claim the per-user slot. We record ourselves first so an evicted holder's disconnect
        # can't clear the new claim, then evict the previous holder (we aren't in the group yet,
        # so the eviction won't hit us). Passive connections only get here when the slot is free.
        previous = _LEAN_HOLDERS.get(self.user_id)
        _LEAN_HOLDERS[self.user_id] = self.channel_name
        if self._takeover and previous and previous != self.channel_name:
            await self.channel_layer.group_send(self.user_group, {"type": "lean.evict"})
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        self.has_claim = True

        try:
            self.process = await asyncio.create_subprocess_exec(
                *sandbox.wrap_argv(cmd, workdir=self.tmpdir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.tmpdir,
                # Long-lived server: strip secrets + isolate, but no CPU-time cap.
                **sandbox.popen_kwargs(cpu_seconds=None),
            )
        except (FileNotFoundError, PermissionError, OSError):
            await self._release_claim()
            self._cleanup_tmp()
            await self.accept()
            await self._send_status(
                "error", "Lean language server could not be started."
            )
            await self.close(code=4003)
            return

        self.reader_task = asyncio.create_task(self._read_from_lean())
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self.accept()

    async def disconnect(self, close_code):
        await self._release_claim()
        for task in (
            getattr(self, "reader_task", None),
            getattr(self, "stderr_task", None),
        ):
            if task:
                task.cancel()
        proc = getattr(self, "process", None)
        if proc:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        self._cleanup_tmp()

    async def lean_evict(self, event):
        """Another window of the same user claimed the Lean slot via "Use Lean here"."""
        await self._send_status("taken_over", "Lean is now open in another window.")
        await self.close(code=WS_CLOSE_TAKEN_OVER)

    async def _release_claim(self):
        """Give up this user's Lean slot — but only if we still hold it (a takeover may have
        reassigned it to another connection already)."""
        if not getattr(self, "has_claim", False):
            return
        self.has_claim = False
        if _LEAN_HOLDERS.get(self.user_id) == self.channel_name:
            del _LEAN_HOLDERS[self.user_id]
        await self.channel_layer.group_discard(self.user_group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        proc = getattr(self, "process", None)
        if not proc or proc.stdin is None or not text_data:
            return
        try:
            message = json.loads(text_data)
        except (ValueError, TypeError):
            return

        method = message.get("method")
        if method in ("textDocument/didOpen", "textDocument/didChange"):
            message = self._rewrite_document_message(message, method)
        elif method == "$/lean/plainGoal":
            self._rewrite_goal_request(message)

        try:
            proc.stdin.write(lean_lsp.frame(message))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError):
            await self.close(code=1011)

    # -- browser -> Lean rewriting --------------------------------------------------------

    def _rewrite_document_message(self, message: dict, method: str) -> dict:
        params = message.setdefault("params", {})
        text_document = params.setdefault("textDocument", {})
        if method == "textDocument/didOpen":
            editable_text = text_document.get("text", "")
        else:  # didChange (full-document sync: last change wins)
            changes = params.get("contentChanges") or [{}]
            editable_text = changes[-1].get("text", "")

        layout = lean_lsp.assemble_document(self.prefix, editable_text, self.suffix)
        self.editable_lines = layout.editable_lines
        version = text_document.get("version", 1)

        if method == "textDocument/didOpen":
            params["textDocument"] = {
                "uri": self.client_uri,
                "languageId": "lean",
                "version": version,
                "text": layout.text,
            }
        else:
            params["textDocument"] = {"uri": self.client_uri, "version": version}
            params["contentChanges"] = [{"text": layout.text}]
        return message

    def _rewrite_goal_request(self, message: dict) -> None:
        params = message.setdefault("params", {})
        params.setdefault("textDocument", {})["uri"] = self.client_uri
        position = params.setdefault("position", {})
        position["line"] = lean_lsp.to_lean_line(
            position.get("line", 0), self.prefix_lines
        )

    # -- Lean -> browser ------------------------------------------------------------------

    async def _read_from_lean(self):
        reader = self.process.stdout
        try:
            while True:
                message = await lean_lsp.read_message(reader)
                if message is None:
                    continue
                if message.get("method") == "textDocument/publishDiagnostics":
                    message = {
                        "jsonrpc": "2.0",
                        "method": "textDocument/publishDiagnostics",
                        "params": lean_lsp.remap_diagnostics(
                            message.get("params", {}),
                            self.prefix_lines,
                            self.editable_lines,
                            self.client_uri,
                        ),
                    }
                try:
                    await self.send(text_data=json.dumps(message))
                except Exception:
                    break
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:
            pass

    async def _drain_stderr(self):
        reader = self.process.stderr
        try:
            while await reader.readline():
                pass
        except (asyncio.CancelledError, Exception):
            pass

    # -- helpers --------------------------------------------------------------------------

    async def _send_status(self, status: str, reason: str):
        await self.send(
            text_data=json.dumps({"pisa": {"status": status, "reason": reason}})
        )

    def _cleanup_tmp(self):
        tmpdir = getattr(self, "tmpdir", None)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @database_sync_to_async
    def _load_context(self, problem_pk, user):
        try:
            problem = Problem.objects.select_related(
                "assignment", "assignment__course"
            ).get(pk=problem_pk)
        except Problem.DoesNotExist:
            return None

        course = problem.assignment.course
        allowed = user.is_staff or (
            problem.assignment.is_published
            and course.students.filter(pk=user.pk).exists()
        )
        if not allowed:
            return None

        blocks = list(problem.blocks.order_by("order"))
        editable = [
            b for b in blocks if b.block_type == ProblemBlock.BLOCK_TYPE_EDITABLE_CODE
        ]
        if len(editable) != 1:
            return {"unsupported": True}
        editable_block = editable[0]

        prefix = ""
        for source_file in problem.assignment.source_files.order_by("pk"):
            if source_file.content:
                prefix += source_file.content + "\n\n"

        suffix_parts = []
        seen_editable = False
        for block in blocks:
            if block.pk == editable_block.pk:
                seen_editable = True
                continue
            if block.block_type == ProblemBlock.BLOCK_TYPE_FIXED_CODE and block.content:
                if seen_editable:
                    suffix_parts.append(block.content + "\n\n")
                else:
                    prefix += block.content + "\n\n"

        # The assembled document appends "\n\n" after every block (incl. the editable one),
        # matching assemble_lean_submission_source(); fixed blocks after the editable follow.
        suffix = "\n\n" + "".join(suffix_parts)
        return {"prefix": prefix, "suffix": suffix}

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
import contextlib
import json
import logging
import shlex
import shutil
import tempfile
from pathlib import Path

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core.cache import cache

from . import lean_lsp, sandbox
from .models import Problem, ProblemBlock

logger = logging.getLogger(__name__)

# At most one live `lean --server` process per user, across all their tabs/windows/devices.
# The current holder's channel name is kept in the Django cache (LocMemCache by default, so
# this is correct for a single ASGI process out of the box; set REDIS_URL to make it correct
# across multiple worker processes too). A second, passive connection is rejected as "busy";
# an explicit `?takeover=1` connection (the editor's "Use Lean here" button) evicts the current
# holder instead. Authentication is required to reach the editor at all, so keying on user id
# means an abuser needs many accounts, not just many tabs.
#
# Claims carry a TTL (LEAN_CAP_TTL) refreshed by a heartbeat while the connection is open, so a
# worker that crashes mid-session doesn't leak a permanently "busy" slot: the entry just expires.

WS_CLOSE_BUSY = 4409
WS_CLOSE_TAKEN_OVER = 4410
# The `lean --server` process died under us (e.g. the sandbox failed to start). Server-sent
# close codes must be 1000 or 3000-4999 (autobahn raises on the "internal error" code 1011).
WS_CLOSE_LEAN_EXITED = 4500


def _user_group(user_id: int) -> str:
    return f"lean-user-{user_id}"


def _lean_holder_key(user_id: int) -> str:
    return f"lean-holder:{user_id}"


class LeanLSPConsumer(AsyncWebsocketConsumer):
    async def _claim_or_reject_busy(self, key: str) -> bool:
        """Non-takeover path: atomically claim the slot, or reject as busy if another window
        already holds it. ``cache.aadd`` is a set-if-absent (Redis SETNX under the real
        backend), so the busy check and the claim are the same atomic operation: no race
        window for two concurrent connects to both slip through, even across worker
        processes. Returns whether this connection claimed the slot."""
        if not await cache.aadd(key, self.channel_name, timeout=settings.LEAN_CAP_TTL):
            await self.accept()
            await self._send_status(
                "busy", "A Pisa Lean instance is open in another window."
            )
            await self.close(code=WS_CLOSE_BUSY)
            return False
        self.has_claim = True
        # Refresh the claim's TTL while the connection is open, so a crashed worker's holder
        # expires instead of leaking a permanently "busy" slot (see LEAN_CAP_TTL).
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        return True

    def _resolve_lsp_cmd(self) -> list[str] | None:
        """LEAN_LSP_CMD from settings, split into argv if given as a string; ``None`` if
        unset."""
        cmd = getattr(settings, "LEAN_LSP_CMD", None)
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        return cmd or None

    async def _commit_takeover(self, key: str) -> bool:
        """Claim the slot for a ``?takeover=1`` connection, evicting whoever holds it now
        that we know this connection will actually proceed (deferred this far so a takeover
        that then fails a later check never stomps a real holder's slot). ``aset``
        unconditionally wins the slot (last write takes it); broadcasting the evict before
        joining the group means we never evict ourselves, and any holder already in the
        group (the real previous holder, or a competing takeover that joined first) gets it
        directly. A second, concurrent takeover that joins the group *after* this broadcast
        would miss it, so the recheck right after group_add catches that case too:
        whichever takeover's ``aset`` happened last is the only one whose recheck still
        finds itself the holder, so it's the only one left standing. Returns whether this
        connection still holds the slot."""
        await cache.aset(key, self.channel_name, timeout=settings.LEAN_CAP_TTL)
        self.has_claim = True
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        await self.channel_layer.group_send(self.user_group, {"type": "lean.evict"})
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        if await cache.aget(key) != self.channel_name:
            self._evicted = True
            await self._release_claim()
            await self.accept()
            await self._send_status("taken_over", "Lean is now open in another window.")
            await self.close(code=WS_CLOSE_TAKEN_OVER)
            return False
        return True

    async def _start_lean_process(self, cmd: list[str]) -> bool:
        """Launch the Lean LSP subprocess. On failure, sends an error status and closes;
        returns whether the connection should proceed."""
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
            return False
        return True

    async def connect(self) -> None:
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=4401)  # unauthenticated
            return

        self.user_id = user.id
        self.user_group = _user_group(self.user_id)
        self.has_claim = False
        takeover = b"takeover=1" in self.scope.get("query_string", b"")
        key = _lean_holder_key(self.user_id)

        # One live Lean instance per user. A passive connection while another is already live is
        # rejected as busy up front; the client greys the editor and offers "Use Lean here",
        # which reconnects with ?takeover=1. Takeover claims the slot further down (see
        # _commit_takeover), only once we know this connection will actually proceed: claiming
        # here, before the permission/support/config checks that follow, would stomp the real
        # holder's cache entry even if this takeover then failed one of those checks, leaving
        # that holder disconnected from its own slot while still believing it holds it.
        if not takeover and not await self._claim_or_reject_busy(key):
            return

        self.problem_pk = self.scope["url_route"]["kwargs"]["problem_pk"]
        context = await self._load_context(self.problem_pk, user)
        if context is None:
            await self._release_claim()
            await self.close(code=4403)  # not allowed / no such problem
            return
        if context.get("unsupported"):
            await self._release_claim()
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

        cmd = self._resolve_lsp_cmd()
        if cmd is None:
            await self._release_claim()
            await self.accept()
            await self._send_status(
                "error", "LEAN_LSP_CMD is not configured on the server."
            )
            await self.close(code=4003)
            return

        if takeover:
            if not await self._commit_takeover(key):
                return
        else:
            await self.channel_layer.group_add(self.user_group, self.channel_name)

        if not await self._start_lean_process(cmd):
            return

        self.reader_task = asyncio.create_task(self._read_from_lean())
        self.stderr_task = asyncio.create_task(self._drain_stderr())
        await self.accept()

    # close_code is part of Channels' fixed disconnect() override signature.
    async def disconnect(self, close_code) -> None:  # noqa: ARG002
        await self._release_claim()
        for task in (
            getattr(self, "reader_task", None),
            getattr(self, "stderr_task", None),
        ):
            if task:
                task.cancel()
        proc = getattr(self, "process", None)
        if proc:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        self._cleanup_tmp()

    # event is part of the channel-layer event-handler signature (see the group_send above).
    async def lean_evict(self, event) -> None:  # noqa: ARG002
        """Another window of the same user claimed the Lean slot via "Use Lean here".

        Two takeovers racing each other can each broadcast an evict, so a given connection may
        receive more than one, and delivery can be delayed enough that a stale broadcast (from
        a takeover that was itself later superseded) arrives after we've already become the
        current holder again. Check the cache rather than trusting the broadcast blindly, and
        ignore anything past the first real eviction, so we never close twice.
        """
        if getattr(self, "_evicted", False):
            return
        key = _lean_holder_key(self.user_id)
        if await cache.aget(key) == self.channel_name:
            return
        self._evicted = True
        await self._send_status("taken_over", "Lean is now open in another window.")
        await self.close(code=WS_CLOSE_TAKEN_OVER)

    async def _heartbeat_loop(self) -> None:
        """Refresh the claim's TTL while we still hold it; stop refreshing (and let it expire)
        once we don't, e.g. after a takeover reassigned it to another connection."""
        key = _lean_holder_key(self.user_id)
        interval = max(settings.LEAN_CAP_TTL // 3, 1)
        while True:
            await asyncio.sleep(interval)
            if await cache.aget(key) != self.channel_name:
                return
            await cache.atouch(key, timeout=settings.LEAN_CAP_TTL)

    async def _release_claim(self) -> None:
        """Give up this user's Lean slot, but only if we still hold it (a takeover may have
        reassigned it to another connection already)."""
        if not getattr(self, "has_claim", False):
            return
        self.has_claim = False
        task = getattr(self, "heartbeat_task", None)
        if task:
            task.cancel()
        key = _lean_holder_key(self.user_id)
        if await cache.aget(key) == self.channel_name:
            await cache.adelete(key)
        await self.channel_layer.group_discard(self.user_group, self.channel_name)

    # bytes_data is part of Channels' fixed receive() signature; this consumer is text-only.
    async def receive(self, text_data=None, bytes_data=None) -> None:  # noqa: ARG002
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
            await self._send_status("error", "The Lean server process has exited.")
            await self.close(code=WS_CLOSE_LEAN_EXITED)

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

    async def _read_from_lean(self) -> None:
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
                    # Expected during an ordinary disconnect race (the browser socket closed
                    # while we were mid-send); debug rather than warning so a normal teardown
                    # doesn't look like an error in the logs.
                    logger.debug(
                        "Failed to forward a Lean message to the browser", exc_info=True
                    )
                    break
        except (asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:
            logger.warning("Lean stdout reader ended unexpectedly", exc_info=True)

    async def _drain_stderr(self) -> None:
        reader = self.process.stderr
        try:
            while await reader.readline():
                pass
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Lean stderr drain ended unexpectedly", exc_info=True)

    # -- helpers --------------------------------------------------------------------------

    async def _send_status(self, status: str, reason: str) -> None:
        await self.send(
            text_data=json.dumps({"pisa": {"status": status, "reason": reason}})
        )

    def _cleanup_tmp(self) -> None:
        tmpdir = getattr(self, "tmpdir", None)
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @database_sync_to_async
    def _load_context(self, problem_pk: int, user) -> dict | None:
        """The assembled document's fixed prefix/suffix for ``problem_pk``, or ``None`` if
        ``user`` may not view it, or ``{"unsupported": True}`` if the problem doesn't have
        exactly one editable block (live feedback only supports that shape)."""
        try:
            problem = Problem.objects.select_related(
                "assignment", "assignment__course"
            ).get(pk=problem_pk)
        except Problem.DoesNotExist:
            return None

        course = problem.assignment.course
        is_course_staff = (
            course.instructors.filter(pk=user.pk).exists()
            or course.tas.filter(pk=user.pk).exists()
        )
        allowed = (
            user.is_staff
            or is_course_staff
            or (
                problem.assignment.is_published
                and course.students.filter(pk=user.pk).exists()
            )
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

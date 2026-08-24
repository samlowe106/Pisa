"""Per-user Lean-instance cap (apps/homework/consumers.py).

The LSP consumer allows at most one live ``lean --server`` per user across all their tabs,
the main guard against a logged-in user spawning unbounded Lean processes. These tests drive
the consumer through ``WebsocketCommunicator`` with a cheap stand-in server command (``sleep``)
and the OS sandbox disabled, so they exercise the claim/evict bookkeeping without needing Lean
or bubblewrap.

DB access happens on a worker thread (``database_sync_to_async``), so these must run under
``TransactionTestCase`` (committed rows are visible across threads); a plain ``TestCase``'s
open transaction would hide the fixtures from the consumer.
"""

import asyncio
import sys

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings

from apps.homework import consumers
from apps.homework.models import Assignment, Problem, ProblemBlock
from apps.homework.routing import websocket_urlpatterns

from .utils import make_role_matrix

# A stand-in `lean --server`: reads Content-Length framed JSON-RPC on stdin (matching
# lean_lsp.frame/read_message exactly) and replies with canned, framed responses, so tests can
# drive the *real* round trip through consumers.py's message rewriting instead of only the pure
# helpers in lean_lsp.py (already covered by test_lean_lsp.py). On didOpen/didChange it always
# replies with two diagnostics, one at full-document line 2 (inside any fixed prefix) and one at
# line 5, so a test that builds a problem with a 5-line prefix can assert the prefix diagnostic
# is dropped and the other survives, shifted back to editor line 0. On $/lean/plainGoal it just
# echoes back the (already-rewritten) line/uri it received, so a test can assert on those.
_FAKE_LSP_SERVER = r"""
import json, re, sys

def read_message():
    headers = b""
    while not headers.endswith(b"\r\n\r\n"):
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        headers += chunk
    match = re.search(rb"Content-Length:\s*(\d+)", headers, re.IGNORECASE)
    if not match:
        return None
    body = sys.stdin.buffer.read(int(match.group(1)))
    return json.loads(body.decode("utf-8"))

def write_message(message):
    body = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
    sys.stdout.buffer.flush()

while True:
    msg = read_message()
    if msg is None:
        break
    method = msg.get("method")
    if method in ("textDocument/didOpen", "textDocument/didChange"):
        write_message({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": "ignored",
                "diagnostics": [
                    {
                        "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 1}},
                        "message": "prefix diagnostic, must not reach the browser",
                        "severity": 1,
                    },
                    {
                        "range": {"start": {"line": 5, "character": 0}, "end": {"line": 5, "character": 3}},
                        "message": "editable diagnostic, must survive",
                        "severity": 1,
                    },
                ],
            },
        })
    elif method == "$/lean/plainGoal":
        write_message({
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "result": {
                "echo_line": msg["params"]["position"]["line"],
                "echo_uri": msg["params"]["textDocument"]["uri"],
            },
        })
"""


async def _open_for(user, problem_pk, *, takeover=False):
    path = f"/ws/lean-lsp/{problem_pk}/"
    if takeover:
        path += "?takeover=1"
    communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), path)
    communicator.scope["user"] = user
    connected, _ = await communicator.connect(timeout=10)
    return communicator, connected


def _holder(user_id):
    return cache.get(consumers._lean_holder_key(user_id))


@override_settings(LEAN_LSP_CMD=["sleep", "30"], LEAN_SANDBOX_ENABLED=False)
class LeanInstanceCapTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.m = make_role_matrix()
        self.user = self.m["student"]
        self.problem_pk = self.m["problem"].pk

    def tearDown(self):
        cache.clear()

    async def _open(self, user, *, takeover=False):
        return await _open_for(user, self.problem_pk, takeover=takeover)

    async def test_first_connection_claims_the_slot_and_releases_on_disconnect(self):
        communicator, connected = await self._open(self.user)
        self.assertTrue(connected)
        self.assertIsNotNone(_holder(self.user.id))
        await communicator.disconnect()
        self.assertIsNone(_holder(self.user.id))

    async def test_second_passive_connection_is_rejected_busy(self):
        first, _ = await self._open(self.user)
        holder = _holder(self.user.id)

        second, connected = await self._open(self.user)
        # Accepted, then told "busy" and closed: the slot is NOT reassigned.
        status = await second.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "busy")
        close = await second.receive_output(timeout=10)
        self.assertEqual(close["type"], "websocket.close")
        self.assertEqual(close["code"], consumers.WS_CLOSE_BUSY)
        self.assertEqual(_holder(self.user.id), holder)  # unchanged

        await first.disconnect()
        await second.disconnect()

    async def test_takeover_evicts_the_current_holder(self):
        first, _ = await self._open(self.user)
        original_holder = _holder(self.user.id)

        second, connected = await self._open(self.user, takeover=True)
        self.assertTrue(connected)

        # The original holder is told it was taken over and closed with 4410.
        status = await first.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "taken_over")
        close = await first.receive_output(timeout=10)
        self.assertEqual(close["code"], consumers.WS_CLOSE_TAKEN_OVER)

        # The slot now belongs to the new connection, not the evicted one.
        self.assertIsNotNone(_holder(self.user.id))
        self.assertNotEqual(_holder(self.user.id), original_holder)

        await first.disconnect()
        await second.disconnect()

    async def test_a_different_user_gets_their_own_slot(self):
        # The cap is per user, so two distinct users can both hold a live instance.
        mine, _ = await self._open(self.user)
        theirs, connected = await self._open(self.m["admin"])
        self.assertTrue(connected)
        self.assertIsNotNone(_holder(self.user.id))
        self.assertIsNotNone(_holder(self.m["admin"].id))
        await mine.disconnect()
        await theirs.disconnect()

    async def test_heartbeat_keeps_the_claim_alive_past_a_single_ttl_window(self):
        with override_settings(LEAN_CAP_TTL=3):
            communicator, connected = await self._open(self.user)
            self.assertTrue(connected)
            await asyncio.sleep(
                4
            )  # past one TTL window, but the heartbeat should refresh it
            self.assertIsNotNone(_holder(self.user.id))
            await communicator.disconnect()

    async def test_claim_expires_without_a_heartbeat(self):
        # Simulate a crashed worker: claim the slot directly, without a live consumer task
        # heartbeating it, and confirm it self-clears once the TTL elapses.
        key = consumers._lean_holder_key(self.user.id)
        with override_settings(LEAN_CAP_TTL=1):
            await cache.aadd(key, "stale-channel", timeout=1)
            self.assertIsNotNone(_holder(self.user.id))
            await asyncio.sleep(1.5)
            self.assertIsNone(_holder(self.user.id))
            communicator, connected = await self._open(self.user)
            self.assertTrue(connected)  # the expired slot no longer blocks a new claim
            await communicator.disconnect()

    async def test_concurrent_connections_do_not_both_claim_the_slot(self):
        # Two connects from the same user race through the same DB-await window inside
        # connect(); the slot must still go to exactly one of them, with the other told
        # "busy" rather than both slipping through and spawning their own Lean process.
        (first, first_ok), (second, second_ok) = await asyncio.gather(
            self._open(self.user), self._open(self.user)
        )
        self.assertTrue(first_ok)
        self.assertTrue(second_ok)  # both get accept()ed; only one stays open

        async def told_busy(comm):
            # No message ever arrives on the connection that actually won the slot, so a
            # timeout here just means "this one won": the test communicator cancels its
            # app task on timeout, which is harmless since we don't touch it again.
            try:
                status = await comm.receive_json_from(timeout=2)
            except TimeoutError:
                return False
            if status.get("pisa", {}).get("status") != "busy":
                return False
            await comm.receive_output(timeout=2)  # drain the close frame that follows
            await comm.disconnect()
            return True

        busy_flags = [await told_busy(first), await told_busy(second)]
        self.assertEqual(busy_flags.count(True), 1)


@override_settings(LEAN_LSP_CMD=["sleep", "30"], LEAN_SANDBOX_ENABLED=False)
class LeanSocketAccessTests(TransactionTestCase):
    """Who may open the live-Lean socket: enrolled students + course staff only (the same gate
    as the HTTP problem page), so the WebSocket isn't a side door around published/enrolment.
    """

    def setUp(self):
        cache.clear()
        self.m = make_role_matrix()

    def tearDown(self):
        cache.clear()

    def _draft_problem(self):
        draft = Assignment.objects.create(
            course=self.m["course"],
            title="Draft",
            slug="draft",
            created_by=self.m["instructor"],
            is_published=False,
        )
        problem = Problem.objects.create(assignment=draft, title="D1", points=1)
        ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
            content="",
            order=0,
        )
        return problem

    async def test_enrolled_student_may_connect(self):
        communicator, connected = await _open_for(
            self.m["student"], self.m["problem"].pk
        )
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_outsider_is_refused(self):
        communicator, connected = await _open_for(
            self.m["outsider"], self.m["problem"].pk
        )
        self.assertFalse(connected)  # closed before accept, no Lean spawned
        self.assertIsNone(_holder(self.m["outsider"].id))

    async def test_student_cannot_reach_a_draft_problem(self):
        problem = await database_sync_to_async(self._draft_problem)()
        communicator, connected = await _open_for(self.m["student"], problem.pk)
        self.assertFalse(
            connected
        )  # unpublished: not accessible over the socket either
        await communicator.disconnect()

    async def test_instructor_may_reach_a_draft_problem(self):
        problem = await database_sync_to_async(self._draft_problem)()
        communicator, connected = await _open_for(self.m["instructor"], problem.pk)
        self.assertTrue(connected)
        await communicator.disconnect()

    async def test_ta_may_reach_a_draft_problem(self):
        problem = await database_sync_to_async(self._draft_problem)()
        communicator, connected = await _open_for(self.m["ta"], problem.pk)
        self.assertTrue(connected)
        await communicator.disconnect()


@override_settings(LEAN_SANDBOX_ENABLED=False)
class LeanLSPMessageRewriteTests(TransactionTestCase):
    """The message-rewrite glue in consumers.py: assembling the document, remapping
    publishDiagnostics back to editor coordinates, and translating $/lean/plainGoal positions.
    Drives the fake LSP server above through real Content-Length framing so the round trip
    exercises _rewrite_document_message/_rewrite_goal_request/_read_from_lean directly, not just
    the pure helpers they call (lean_lsp.py, covered by test_lean_lsp.py)."""

    def setUp(self):
        cache.clear()
        self.m = make_role_matrix()
        # A fixed block ending "line_a\nline_b\nline_c\n" (3 newlines) followed by the appended
        # "\n\n" separator (2 more) gives a known prefix_lines of 5, matching the fake server's
        # canned diagnostic lines above.
        problem = Problem.objects.create(
            assignment=self.m["assignment"], title="LSP", points=1
        )
        ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_FIXED_CODE,
            content="line_a\nline_b\nline_c\n",
            order=0,
        )
        ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
            content="",
            order=1,
        )
        self.problem_pk = problem.pk

    def tearDown(self):
        cache.clear()

    @override_settings(LEAN_LSP_CMD=[sys.executable, "-c", _FAKE_LSP_SERVER])
    async def test_did_open_diagnostics_are_remapped_to_editor_coordinates(self):
        communicator, connected = await _open_for(self.m["student"], self.problem_pk)
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                "method": "textDocument/didOpen",
                "params": {"textDocument": {"text": "example : True := trivial"}},
            }
        )
        message = await communicator.receive_json_from(timeout=10)
        self.assertEqual(message["method"], "textDocument/publishDiagnostics")
        diagnostics = message["params"]["diagnostics"]
        # The prefix-region diagnostic (full-document line 2) is dropped; only the editable
        # one (line 5) survives, shifted back to editor line 0.
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["range"]["start"]["line"], 0)
        self.assertEqual(diagnostics[0]["message"], "editable diagnostic, must survive")

        await communicator.disconnect()

    @override_settings(LEAN_LSP_CMD=[sys.executable, "-c", _FAKE_LSP_SERVER])
    async def test_plain_goal_request_position_is_translated_to_full_document_coordinates(
        self,
    ):
        communicator, connected = await _open_for(self.m["student"], self.problem_pk)
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                "id": 1,
                "method": "$/lean/plainGoal",
                "params": {
                    "textDocument": {"uri": "ignored"},
                    "position": {"line": 0, "character": 3},
                },
            }
        )
        message = await communicator.receive_json_from(timeout=10)
        # Editor line 0 lands at full-document line 5, the known prefix_lines from setUp; the
        # client-supplied uri is discarded in favour of the server's own client_uri.
        self.assertEqual(message["result"]["echo_line"], 5)
        self.assertTrue(
            message["result"]["echo_uri"].endswith(
                f"pisa_problem_{self.problem_pk}.lean"
            )
        )

        await communicator.disconnect()

    @override_settings(LEAN_LSP_CMD=[sys.executable, "-c", _FAKE_LSP_SERVER])
    async def test_did_change_diagnostics_are_remapped_too(self):
        # didChange takes the *last* entry of contentChanges (full-document sync); otherwise
        # identical rewriting to didOpen, exercised separately since it's a different branch.
        communicator, connected = await _open_for(self.m["student"], self.problem_pk)
        self.assertTrue(connected)

        await communicator.send_json_to(
            {
                "method": "textDocument/didChange",
                "params": {
                    "textDocument": {"version": 2},
                    "contentChanges": [{"text": "example : True := trivial"}],
                },
            }
        )
        message = await communicator.receive_json_from(timeout=10)
        diagnostics = message["params"]["diagnostics"]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["range"]["start"]["line"], 0)

        await communicator.disconnect()

    async def test_malformed_json_from_the_browser_is_ignored_not_fatal(self):
        # receive() must swallow a non-JSON frame rather than crash the consumer; a later,
        # well-formed message on the same connection should still get a normal response.
        with override_settings(LEAN_LSP_CMD=[sys.executable, "-c", _FAKE_LSP_SERVER]):
            communicator, connected = await _open_for(
                self.m["student"], self.problem_pk
            )
            self.assertTrue(connected)

            await communicator.send_to(text_data="not valid json{")
            await communicator.send_json_to(
                {
                    "method": "textDocument/didOpen",
                    "params": {"textDocument": {"text": "example : True := trivial"}},
                }
            )
            message = await communicator.receive_json_from(timeout=10)
            self.assertEqual(message["method"], "textDocument/publishDiagnostics")

            await communicator.disconnect()


@override_settings(LEAN_LSP_CMD=["sleep", "30"], LEAN_SANDBOX_ENABLED=False)
class LeanLSPConnectErrorTests(TransactionTestCase):
    """The failure paths in connect(): each one must still release the Lean-instance claim it
    took before failing, so a rejected connection never leaks a permanently "busy" slot.
    """

    def setUp(self):
        cache.clear()
        self.m = make_role_matrix()

    def tearDown(self):
        cache.clear()

    async def test_problem_with_two_editable_blocks_is_unsupported(self):
        def _make_problem():
            problem = Problem.objects.create(
                assignment=self.m["assignment"], title="Two", points=1
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
                content="",
                order=0,
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
                content="",
                order=1,
            )
            return problem

        problem = await database_sync_to_async(_make_problem)()
        communicator, connected = await _open_for(self.m["student"], problem.pk)
        self.assertTrue(connected)  # accepted, then told unsupported and closed
        status = await communicator.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "unsupported")
        close = await communicator.receive_output(timeout=10)
        self.assertEqual(close["code"], 4002)
        self.assertIsNone(_holder(self.m["student"].id))  # claim released, not leaked

    async def test_nonexistent_problem_is_refused(self):
        communicator, connected = await _open_for(self.m["student"], 999999999)
        self.assertFalse(connected)  # closed before accept: no such problem
        self.assertIsNone(_holder(self.m["student"].id))

    async def test_unauthenticated_connection_is_closed(self):
        path = f"/ws/lean-lsp/{self.m['problem'].pk}/"
        communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), path)
        # No scope["user"] set at all: .get("user") is None, same as an anonymous request.
        connected, _ = await communicator.connect(timeout=10)
        self.assertFalse(connected)

    @override_settings(LEAN_LSP_CMD=None)
    async def test_missing_lean_lsp_cmd_reports_a_server_error(self):
        communicator, connected = await _open_for(
            self.m["student"], self.m["problem"].pk
        )
        self.assertTrue(connected)
        status = await communicator.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "error")
        close = await communicator.receive_output(timeout=10)
        self.assertEqual(close["code"], 4003)
        self.assertIsNone(_holder(self.m["student"].id))

    @override_settings(LEAN_LSP_CMD=["/nonexistent/pisa-lean-lsp-binary"])
    async def test_lean_process_spawn_failure_reports_a_server_error(self):
        communicator, connected = await _open_for(
            self.m["student"], self.m["problem"].pk
        )
        self.assertTrue(connected)
        status = await communicator.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "error")
        close = await communicator.receive_output(timeout=10)
        self.assertEqual(close["code"], 4003)
        self.assertIsNone(_holder(self.m["student"].id))

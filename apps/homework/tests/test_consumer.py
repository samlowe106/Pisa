"""Per-user Lean-instance cap (apps/homework/consumers.py).

The LSP consumer allows at most one live ``lean --server`` per user across all their tabs —
the main guard against a logged-in user spawning unbounded Lean processes. These tests drive
the consumer through ``WebsocketCommunicator`` with a cheap stand-in server command (``sleep``)
and the OS sandbox disabled, so they exercise the claim/evict bookkeeping without needing Lean
or bubblewrap.

DB access happens on a worker thread (``database_sync_to_async``), so these must run under
``TransactionTestCase`` (committed rows are visible across threads); a plain ``TestCase``'s
open transaction would hide the fixtures from the consumer.
"""

from channels.db import database_sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings

from apps.homework import consumers
from apps.homework.models import Assignment, Problem, ProblemBlock
from apps.homework.routing import websocket_urlpatterns

from .utils import make_role_matrix


async def _open_for(user, problem_pk, *, takeover=False):
    path = f"/ws/lean-lsp/{problem_pk}/"
    if takeover:
        path += "?takeover=1"
    communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), path)
    communicator.scope["user"] = user
    connected, _ = await communicator.connect(timeout=10)
    return communicator, connected


@override_settings(LEAN_LSP_CMD=["sleep", "30"], LEAN_SANDBOX_ENABLED=False)
class LeanInstanceCapTests(TransactionTestCase):
    def setUp(self):
        consumers._LEAN_HOLDERS.clear()
        self.m = make_role_matrix()
        self.user = self.m["student"]
        self.problem_pk = self.m["problem"].pk

    def tearDown(self):
        consumers._LEAN_HOLDERS.clear()

    async def _open(self, user, *, takeover=False):
        return await _open_for(user, self.problem_pk, takeover=takeover)

    async def test_first_connection_claims_the_slot_and_releases_on_disconnect(self):
        communicator, connected = await self._open(self.user)
        self.assertTrue(connected)
        self.assertIn(self.user.id, consumers._LEAN_HOLDERS)
        await communicator.disconnect()
        self.assertNotIn(self.user.id, consumers._LEAN_HOLDERS)

    async def test_second_passive_connection_is_rejected_busy(self):
        first, _ = await self._open(self.user)
        holder = consumers._LEAN_HOLDERS[self.user.id]

        second, connected = await self._open(self.user)
        # Accepted, then told "busy" and closed — the slot is NOT reassigned.
        status = await second.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "busy")
        close = await second.receive_output(timeout=10)
        self.assertEqual(close["type"], "websocket.close")
        self.assertEqual(close["code"], consumers.WS_CLOSE_BUSY)
        self.assertEqual(consumers._LEAN_HOLDERS[self.user.id], holder)  # unchanged

        await first.disconnect()
        await second.disconnect()

    async def test_takeover_evicts_the_current_holder(self):
        first, _ = await self._open(self.user)
        original_holder = consumers._LEAN_HOLDERS[self.user.id]

        second, connected = await self._open(self.user, takeover=True)
        self.assertTrue(connected)

        # The original holder is told it was taken over and closed with 4410.
        status = await first.receive_json_from(timeout=10)
        self.assertEqual(status["pisa"]["status"], "taken_over")
        close = await first.receive_output(timeout=10)
        self.assertEqual(close["code"], consumers.WS_CLOSE_TAKEN_OVER)

        # The slot now belongs to the new connection, not the evicted one.
        self.assertIn(self.user.id, consumers._LEAN_HOLDERS)
        self.assertNotEqual(consumers._LEAN_HOLDERS[self.user.id], original_holder)

        await first.disconnect()
        await second.disconnect()

    async def test_a_different_user_gets_their_own_slot(self):
        # The cap is per user, so two distinct users can both hold a live instance.
        mine, _ = await self._open(self.user)
        theirs, connected = await self._open(self.m["admin"])
        self.assertTrue(connected)
        self.assertIn(self.user.id, consumers._LEAN_HOLDERS)
        self.assertIn(self.m["admin"].id, consumers._LEAN_HOLDERS)
        await mine.disconnect()
        await theirs.disconnect()


@override_settings(LEAN_LSP_CMD=["sleep", "30"], LEAN_SANDBOX_ENABLED=False)
class LeanSocketAccessTests(TransactionTestCase):
    """Who may open the live-Lean socket: enrolled students + course staff only (the same gate
    as the HTTP problem page), so the WebSocket isn't a side door around published/enrolment.
    """

    def setUp(self):
        consumers._LEAN_HOLDERS.clear()
        self.m = make_role_matrix()

    def tearDown(self):
        consumers._LEAN_HOLDERS.clear()

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
        self.assertNotIn(self.m["outsider"].id, consumers._LEAN_HOLDERS)

    async def test_student_cannot_reach_a_draft_problem(self):
        problem = await database_sync_to_async(self._draft_problem)()
        communicator, connected = await _open_for(self.m["student"], problem.pk)
        self.assertFalse(
            connected
        )  # unpublished → not accessible over the socket either
        await communicator.disconnect()

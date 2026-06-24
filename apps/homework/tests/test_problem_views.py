"""Coverage for apps/homework/views/problems.py — the pure helpers (pager, Lean-output parsing,
executable resolution) and the run/submit/detail request handlers."""

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.homework.models import LeanSourceFile, Problem, Submission
from apps.homework.views.problems import (
    build_problem_pager,
    get_lean_executable,
    parse_lean_feedback,
)

from .utils import make_role_matrix, requires_lean


class PagerTests(SimpleTestCase):
    def test_single_problem_has_no_pager(self):
        self.assertIsNone(build_problem_pager(1, 1))

    def test_window_near_the_start_snaps_and_absorbs_edges(self):
        pager = build_problem_pager(1, 4)
        self.assertIsNone(pager["prev"])
        self.assertEqual(pager["next"], 2)
        self.assertFalse(pager["show_first"])
        self.assertEqual(pager["numbers"], [1, 2, 3, 4])  # hi==total-1 absorbed

    def test_window_near_the_end(self):
        pager = build_problem_pager(4, 4)
        self.assertEqual(pager["numbers"], [1, 2, 3, 4])  # lo==2 absorbed to 1
        self.assertIsNone(pager["next"])

    def test_window_in_the_middle_shows_both_jumps(self):
        pager = build_problem_pager(5, 10)
        self.assertEqual(pager["numbers"], [4, 5, 6])
        self.assertTrue(pager["show_first"])
        self.assertTrue(pager["show_last"])

    def test_long_list_at_the_high_end(self):
        pager = build_problem_pager(9, 10)
        self.assertEqual(pager["numbers"], [8, 9, 10])
        self.assertTrue(pager["show_first"])
        self.assertFalse(pager["show_last"])


class ParseLeanFeedbackTests(SimpleTestCase):
    def test_classifies_each_line_kind(self):
        parsed = parse_lean_feedback(
            stdout="goal: ⊢ True\nmsg: hello\nplain note",
            stderr="error: boom\nwarning: careful\n⊢ leftover",
        )
        self.assertIn("⊢ True", parsed["goals"])
        self.assertIn("⊢ leftover", parsed["goals"])
        self.assertIn("hello", parsed["messages"])
        self.assertTrue(any("error: boom" in e for e in parsed["errors"]))
        self.assertTrue(any("careful" in m for m in parsed["messages"]))

    def test_clean_success_gets_a_friendly_message(self):
        parsed = parse_lean_feedback(stdout="", stderr="", returncode=0)
        self.assertEqual(parsed["messages"], ["Lean ran with no errors :)"])

    def test_success_with_bare_stdout_keeps_it(self):
        parsed = parse_lean_feedback(stdout="   ", stderr="", returncode=0)
        self.assertEqual(parsed["messages"], ["Lean ran with no errors :)"])


class GetLeanExecutableTests(SimpleTestCase):
    @override_settings(LEAN_EXECUTABLE="/usr/bin/true")
    def test_explicit_setting_wins(self):
        self.assertEqual(get_lean_executable(), "/usr/bin/true")


class ProblemDetailViewTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        # A second problem so the detail page renders a pager.
        Problem.objects.create(
            assignment=self.m["assignment"], title="P2", points=1, order=1
        )
        # A source file imported by the assignment and shown on problem 1, so the detail page
        # exercises both the staff (tagged-visibility) and student (visible-only) file lists.
        source = LeanSourceFile.objects.create(
            title="Prelude", slug="prelude", created_by=self.m["instructor"]
        )
        self.m["assignment"].source_files.add(source)
        self.m["problem"].visible_source_files.add(source)

    def test_staff_detail_renders_with_pager_and_source_files(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.get("/courses/test-course/hw1/1/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["pager"])  # 2 problems -> pager shown
        self.assertTrue(response.context["can_edit"])

    def test_student_detail_renders(self):
        self.client.force_login(self.m["student"])
        response = self.client.get("/courses/test-course/hw1/2/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])


class ProblemRunSubmitTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.run_url = reverse(
            "homework:problem_run", kwargs={"pk": self.m["problem"].pk}
        )
        self.submit_url = reverse(
            "homework:problem_submit", kwargs={"pk": self.m["problem"].pk}
        )

    def test_run_without_code_is_a_bad_request(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(self.client.post(self.run_url, {}).status_code, 400)

    def test_submit_without_code_is_a_bad_request(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(self.client.post(self.submit_url, {}).status_code, 400)

    @requires_lean
    @override_settings(LEAN_SANDBOX_WRAPPER=[])  # Layer 1 only: don't depend on bwrap
    def test_run_returns_feedback_json(self):
        self.client.force_login(self.m["student"])
        response = self.client.post(
            self.run_url, {"code": "theorem t : True := trivial"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("messages", response.json())

    @requires_lean
    @override_settings(LEAN_SANDBOX_WRAPPER=[])
    def test_submit_records_a_passing_submission(self):
        self.client.force_login(self.m["student"])
        response = self.client.post(
            self.submit_url, {"code": "theorem t : True := trivial"}
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], Submission.STATUS_PASSED)
        self.assertEqual(payload["score"], self.m["problem"].points)
        self.assertTrue(
            Submission.objects.filter(
                problem=self.m["problem"], user=self.m["student"]
            ).exists()
        )


class ProblemCreateUpdateViewTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def test_create_page_renders_for_instructor(self):
        self.client.force_login(self.m["instructor"])
        url = reverse(
            "homework:problem_create",
            kwargs={"course_slug": "test-course", "assignment_slug": "hw1"},
        )
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_update_page_renders_for_instructor(self):
        self.client.force_login(self.m["instructor"])
        url = reverse(
            "homework:problem_update",
            kwargs={
                "course_slug": "test-course",
                "assignment_slug": "hw1",
                "number": 1,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["assignment"], self.m["assignment"])

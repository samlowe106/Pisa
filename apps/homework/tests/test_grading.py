"""Anti-cheat / grading tests (apps/homework/lean_policy.py + lean_runner.py).

Two enforcement layers are exercised:

* the cheap pre-run text scan (``lean_policy.scan``) that rejects disallowed constructs in the
  *student's* editable code before Lean runs, and
* the un-evadable ``#print axioms`` backstop (``forbidden_axioms``) that audits a real compile —
  it catches ``sorry`` even when the text scan is told to allow it (``@requires_lean``).

Most tests need neither Lean nor a DB; the backstop tests use real Lean (skip otherwise).
"""

from django.test import SimpleTestCase, TestCase, override_settings

from apps.homework import lean_policy
from apps.homework.lean_runner import (
    assemble_lean_submission_source,
    grade_lean_submission,
    sanitize_lean_output,
)
from apps.homework.models import Problem, ProblemBlock, Submission

from .utils import make_role_matrix, requires_lean

# Message fragments the grader returns, pinned so a wording change is a conscious test update.
SCAN_REJECTION = "aren't allowed here"
SORRY_REJECTION = "relies on `sorry`"


class PolicyScanTests(SimpleTestCase):
    """``lean_policy.scan`` — the literal pre-run text scan over student code."""

    def _ids(self, code, **kwargs):
        return {rule.id for rule in lean_policy.scan(code, **kwargs)}

    def test_catches_one_construct_per_category(self):
        cases = {
            "theorem t : False := sorry": "sorry",  # UNSOUND
            "axiom bad : False": "axiom",  # UNSOUND
            "def f := native_decide": "native_decide",  # UNSOUND
            "def f := IO.FS.readFile": "io_fs",  # SYSTEM
            "def f : Socket := s": "socket",  # NETWORK
            "#eval dangerous ()": "eval",  # ESCAPE
        }
        for code, expected_id in cases.items():
            self.assertIn(expected_id, self._ids(code), code)

    def test_clean_code_has_no_hits(self):
        self.assertEqual(self._ids("theorem t : True := trivial"), set())

    def test_allowed_set_re_permits_a_rule(self):
        self.assertIn("eval", self._ids("#eval 1"))
        self.assertNotIn("eval", self._ids("#eval 1", allowed=frozenset({"eval"})))


class AxiomBackstopParsingTests(SimpleTestCase):
    """``parse_axioms`` / ``forbidden_axioms`` — the post-compile soundness audit."""

    def test_parse_axioms_reads_the_report(self):
        out = "'t' depends on axioms: [propext, Classical.choice]"
        self.assertEqual(
            lean_policy.parse_axioms(out),
            frozenset({"propext", "Classical.choice"}),
        )

    def test_parse_axioms_handles_no_dependency_and_no_report(self):
        self.assertEqual(
            lean_policy.parse_axioms("'t' does not depend on any axioms"), frozenset()
        )
        self.assertIsNone(lean_policy.parse_axioms("nothing relevant here"))

    def test_only_standard_axioms_are_permitted(self):
        clean = "'t' depends on axioms: [propext, Classical.choice, Quot.sound]"
        self.assertEqual(lean_policy.forbidden_axioms(clean), frozenset())

    def test_sorry_axiom_is_always_forbidden(self):
        out = "'t' depends on axioms: [propext, sorryAx]"
        self.assertEqual(lean_policy.forbidden_axioms(out), frozenset({"sorryAx"}))

    def test_allowed_extends_the_permitted_set(self):
        out = "'t' depends on axioms: [propext, Foo.bar]"
        self.assertEqual(
            lean_policy.forbidden_axioms(out, allowed=frozenset({"Foo.bar"})),
            frozenset(),
        )

    def test_missing_report_is_none(self):
        self.assertIsNone(lean_policy.forbidden_axioms("no axiom report present"))


class SanitizeLeanOutputTests(SimpleTestCase):
    def test_strips_info_lines_but_keeps_errors(self):
        raw = "info: building\nerror: unsolved goals\n\n   trailing"
        cleaned = sanitize_lean_output(raw)
        self.assertNotIn("info: building", cleaned)
        self.assertIn("error: unsolved goals", cleaned)
        self.assertNotIn("\n\n", cleaned)  # blank lines collapsed

    def test_keep_internal_passes_output_through(self):
        raw = "info: building\nerror: boom"
        self.assertEqual(sanitize_lean_output(raw, keep_internal=True), raw)

    def test_empty_is_empty(self):
        self.assertEqual(sanitize_lean_output(""), "")
        self.assertEqual(sanitize_lean_output(None), "")


class AssembleSubmissionSourceTests(TestCase):
    """``assemble_lean_submission_source`` must hand policy a *student-only* view of the code
    so the instructor's (legitimately ``axiom``-using) fixed prefix is never scanned."""

    def setUp(self):
        self.m = make_role_matrix()

    def _problem_with_blocks(self):
        problem = Problem.objects.create(
            assignment=self.m["assignment"], title="P", points=1
        )
        fixed = ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_FIXED_CODE,
            content="axiom instructor_prefix : True",
            order=0,
        )
        editable = ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
            content="",
            order=1,
        )
        return problem, fixed, editable

    def test_student_code_excludes_instructor_prefix(self):
        problem, _fixed, editable = self._problem_with_blocks()
        post = {f"editable_code_{editable.pk}": "theorem mine : True := trivial"}
        full, student, error = assemble_lean_submission_source(problem, post)
        self.assertIsNone(error)
        self.assertIn("axiom instructor_prefix", full)  # compiled doc has the prefix
        self.assertNotIn("axiom", student)  # ...but the scanned view does not
        self.assertIn("theorem mine", full)
        self.assertIn("theorem mine", student)

    def test_single_editable_block_falls_back_to_code_field(self):
        problem = Problem.objects.create(
            assignment=self.m["assignment"], title="Solo", points=1
        )
        ProblemBlock.objects.create(
            problem=problem,
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
            content="",
            order=0,
        )
        full, student, error = assemble_lean_submission_source(
            problem, {"code": "theorem solo : True := trivial"}
        )
        self.assertIsNone(error)
        self.assertIn("theorem solo", student)

    def test_missing_editable_block_is_an_error(self):
        problem, _fixed, _editable = self._problem_with_blocks()
        _full, _student, error = assemble_lean_submission_source(problem, {})
        self.assertIsNotNone(error)
        self.assertIn("Missing submission", error)

    def test_problem_without_editable_blocks_uses_raw_code(self):
        # No editable blocks at all -> the posted "code" field is taken as the whole document.
        problem = Problem.objects.create(
            assignment=self.m["assignment"], title="Plain", points=1
        )
        full, student, error = assemble_lean_submission_source(
            problem, {"code": "theorem plain : True := trivial"}
        )
        self.assertIsNone(error)
        self.assertEqual(full, student)
        self.assertIn("theorem plain", full)


class GradePreScanTests(TestCase):
    """``grade_lean_submission`` gates that fire *before* Lean runs — no Lean needed."""

    def setUp(self):
        self.m = make_role_matrix()

    def _problem(self, **kwargs):
        return Problem.objects.create(
            assignment=self.m["assignment"], title="P", points=1, **kwargs
        )

    def test_required_code_must_be_present(self):
        problem = self._problem(required_code="omega")
        status, message = grade_lean_submission(
            problem, "theorem t : True := trivial", "theorem t : True := trivial"
        )
        self.assertEqual(status, Submission.STATUS_FAILED)
        self.assertIn("required code", message)

    def test_disallowed_construct_in_student_code_is_rejected_before_lean(self):
        problem = self._problem()
        code = "theorem t : False := sorry"
        status, message = grade_lean_submission(problem, code, code)
        self.assertEqual(status, Submission.STATUS_FAILED)
        self.assertIn(SCAN_REJECTION, message)
        self.assertIn("sorry", message)

    def test_allowed_construct_passes_the_scan(self):
        # With `sorry` allow-listed the scan must not reject; whether Lean is installed only
        # changes the *later* outcome, never this pre-run gate.
        problem = self._problem(allowed_constructs=["sorry"])
        code = "theorem t : True := sorry"
        _status, message = grade_lean_submission(problem, code, code)
        self.assertNotIn(SCAN_REJECTION, message)

    def test_scan_ignores_constructs_in_the_instructor_prefix(self):
        # `axiom` appears only in the full (instructor) code, not the student view, so the
        # student-scoped scan must not reject the submission.
        problem = self._problem()
        full = "axiom instructor : False\n\ntheorem t : True := trivial"
        student = "theorem t : True := trivial"
        _status, message = grade_lean_submission(problem, full, student)
        self.assertNotIn(SCAN_REJECTION, message)

    @override_settings(
        LEAN_EXECUTABLE="/nonexistent/lean-binary", LEAN_SANDBOX_WRAPPER=[]
    )
    def test_missing_lean_executable_reports_error_status(self):
        problem = self._problem()
        code = "theorem t : True := trivial"
        status, message = grade_lean_submission(problem, code, code)
        self.assertEqual(status, Submission.STATUS_ERROR)
        self.assertIn("Lean executable not found", message)


@requires_lean
@override_settings(
    LEAN_SANDBOX_WRAPPER=[]
)  # Layer 1 only: the grading logic doesn't need bwrap
class GradeWithRealLeanTests(TestCase):
    """The un-evadable backstop: a real compile + ``#print axioms`` audit. Runs Lean under the
    Layer 1 sandbox so it exercises grading wherever Lean exists (bwrap isolation is tested in
    test_sandbox.py)."""

    def setUp(self):
        self.m = make_role_matrix()

    def _problem(self, **kwargs):
        return Problem.objects.create(
            assignment=self.m["assignment"], title="P", points=1, **kwargs
        )

    def test_correct_proof_passes(self):
        problem = self._problem()
        code = "theorem t : True := trivial"
        status, _message = grade_lean_submission(problem, code, code)
        self.assertEqual(status, Submission.STATUS_PASSED)

    def test_axiom_backstop_catches_sorry_even_when_scan_allows_it(self):
        # allow-list `sorry` so the text scan passes; the axiom audit must still fail it.
        problem = self._problem(axiom_target="t", allowed_constructs=["sorry"])
        code = "theorem t : True := sorry"
        status, message = grade_lean_submission(problem, code, code)
        self.assertEqual(status, Submission.STATUS_FAILED)
        self.assertIn(SORRY_REJECTION, message)

    def test_axiom_target_clean_proof_passes(self):
        problem = self._problem(axiom_target="t")
        code = "theorem t : True := trivial"
        status, _message = grade_lean_submission(problem, code, code)
        self.assertEqual(status, Submission.STATUS_PASSED)

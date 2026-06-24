"""Model-level domain logic: validators, display/grade helpers, the renew-course clone, and
the visibility query helpers. Pure DB logic, no Lean."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.homework.models import (
    Assignment,
    Course,
    LeanSourceFile,
    Problem,
    Submission,
    accessible_problems,
    course_family,
    editable_assignments,
    editable_problems,
    is_student_anywhere,
    renew_course,
    validate_assignment_slug,
    validate_course_slug,
)

from .utils import make_role_matrix


class SlugValidatorTests(TestCase):
    def test_reserved_slugs_raise(self):
        with self.assertRaises(ValidationError):
            validate_course_slug("create")
        for reserved in ("edit", "enroll", "export", "problems", "new"):
            with self.assertRaises(ValidationError):
                validate_assignment_slug(reserved)

    def test_ordinary_slugs_pass(self):
        self.assertIsNone(validate_course_slug("intro-lean"))
        self.assertIsNone(validate_assignment_slug("hw1"))

    def test_validator_is_wired_into_full_clean(self):
        course = Course(title="X", slug="create")
        with self.assertRaises(ValidationError):
            course.full_clean()


class CourseGradeHelperTests(TestCase):
    def test_display_name_qualifies_with_term_and_section(self):
        self.assertEqual(Course(title="Logic").display_name, "Logic")
        self.assertEqual(
            Course(title="Logic", term="Summer 2026").display_name,
            "Logic (Summer 2026)",
        )
        self.assertEqual(
            Course(title="Logic", term="Summer 2026", section="002").display_name,
            "Logic (Summer 2026 · 002)",
        )

    def test_letter_for_default_bands(self):
        course = Course()  # defaults 90/80/70/60
        self.assertEqual(course.letter_for(95)[0], "A")
        self.assertEqual(course.letter_for(90)[0], "A")  # boundary is inclusive
        self.assertEqual(course.letter_for(89.9)[0], "B")
        self.assertEqual(course.letter_for(60)[0], "D")
        self.assertEqual(course.letter_for(59.9)[0], "F")
        self.assertEqual(course.letter_for(0)[0], "F")

    def test_letter_for_custom_bands(self):
        course = Course(grade_a_min=50, grade_b_min=40, grade_c_min=30, grade_d_min=20)
        self.assertEqual(course.letter_for(55)[0], "A")
        self.assertEqual(course.letter_for(25)[0], "D")
        self.assertEqual(course.letter_for(19)[0], "F")


class SubmissionIsLateTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.problem = self.m["problem"]

    def _submit(self):
        return Submission.objects.create(
            problem=self.problem, user=self.m["student"], code="x"
        )

    def test_not_late_without_due_date(self):
        self.assertFalse(self._submit().is_late)

    def test_not_late_before_due(self):
        self.m["assignment"].due_date = timezone.now() + timedelta(hours=1)
        self.m["assignment"].save()
        self.assertFalse(self._submit().is_late)

    def test_late_after_due(self):
        self.m["assignment"].due_date = timezone.now() - timedelta(hours=1)
        self.m["assignment"].save()
        self.assertTrue(self._submit().is_late)


class ProblemPositionAndNameTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.assignment = self.m["assignment"]
        # make_role_matrix already created one problem (P1, order 0); add two more.
        self.p1 = self.m["problem"]
        self.p2 = Problem.objects.create(
            assignment=self.assignment, title="", points=1, order=1
        )
        self.p3 = Problem.objects.create(
            assignment=self.assignment, title="", points=1, order=2
        )

    def test_position_is_one_based_in_order(self):
        self.assertEqual([p.position for p in (self.p1, self.p2, self.p3)], [1, 2, 3])

    def test_position_follows_reorder(self):
        self.p1.order = 5
        self.p1.save()
        # p1 now sorts last -> position 3.
        self.assertEqual(self.p1.position, 3)

    def test_display_name_uses_title_else_position(self):
        self.assertEqual(self.p2.display_name, "Problem 2")
        self.p2.title = "Warm-up"
        self.assertEqual(self.p2.display_name, "Warm-up")
        self.assertEqual(Problem().display_name, "Problem")  # unsaved

    def test_position_is_query_free_when_prefetched(self):
        assignment = Assignment.objects.prefetch_related("problems").get(
            pk=self.assignment.pk
        )
        problems = list(assignment.problems.all())
        for problem in problems:
            problem.assignment = assignment  # wire the cached relation
        with self.assertNumQueries(0):
            self.assertEqual([p.position for p in problems], [1, 2, 3])

    def test_get_absolute_urls_are_nested(self):
        self.assertEqual(self.m["course"].get_absolute_url(), "/courses/test-course/")
        self.assertEqual(
            self.assignment.get_absolute_url(), "/courses/test-course/hw1/"
        )
        self.assertEqual(self.p1.get_absolute_url(), "/courses/test-course/hw1/1/")


class RenewCourseTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.course = self.m["course"]
        self.course.scoring_method = Course.SCORING_SUPERSCORE
        self.course.grade_a_min = 88
        self.course.save()

        self.source = LeanSourceFile.objects.create(
            title="Prelude",
            slug="prelude",
            content="-- lib",
            created_by=self.m["instructor"],
        )
        self.assignment = self.m["assignment"]
        self.assignment.due_date = timezone.now() + timedelta(days=7)
        self.assignment.save()
        self.assignment.source_files.add(self.source)
        self.problem = self.m["problem"]
        self.problem.visible_source_files.add(self.source)
        # A student submission that must NOT be carried into the renewed offering.
        Submission.objects.create(
            problem=self.problem,
            user=self.m["student"],
            code="x",
            status=Submission.STATUS_PASSED,
        )
        self.new = renew_course(
            self.course,
            term="Fall 2026",
            section="002",
            created_by=self.m["instructor"],
        )

    def test_new_offering_metadata(self):
        self.assertNotEqual(self.new.pk, self.course.pk)
        self.assertNotEqual(self.new.slug, self.course.slug)  # unique slug
        self.assertEqual(self.new.renewed_from_id, self.course.pk)
        self.assertEqual(self.new.term, "Fall 2026")
        self.assertEqual(self.new.section, "002")
        # Settings carried over.
        self.assertEqual(self.new.scoring_method, Course.SCORING_SUPERSCORE)
        self.assertEqual(self.new.grade_a_min, 88)

    def test_staff_carried_students_and_submissions_not(self):
        self.assertIn(self.m["instructor"], self.new.instructors.all())
        self.assertIn(self.m["ta"], self.new.tas.all())
        self.assertEqual(self.new.students.count(), 0)
        self.assertEqual(
            Submission.objects.filter(problem__assignment__course=self.new).count(), 0
        )

    def test_content_cloned_with_due_dates_cleared(self):
        new_assignment = self.new.assignments.get(slug="hw1")
        self.assertIsNone(new_assignment.due_date)  # fresh term
        self.assertIn(self.source, new_assignment.source_files.all())
        new_problem = new_assignment.problems.get()
        self.assertNotEqual(new_problem.pk, self.problem.pk)
        self.assertIn(self.source, new_problem.visible_source_files.all())
        # Blocks deep-copied.
        self.assertEqual(new_problem.blocks.count(), self.problem.blocks.count())
        self.assertEqual(
            new_problem.blocks.first().content, self.problem.blocks.first().content
        )


class CourseFamilyTests(TestCase):
    def test_standalone_course_is_a_family_of_one(self):
        m = make_role_matrix()
        self.assertEqual(course_family(m["course"]), [m["course"]])

    def test_family_spans_the_renew_chain_oldest_first(self):
        m = make_role_matrix()
        root = m["course"]
        gen2 = renew_course(root, term="T2", section="", created_by=m["instructor"])
        gen3 = renew_course(gen2, term="T3", section="", created_by=m["instructor"])
        # Asking any member returns the whole lineage, oldest first.
        for member in (root, gen2, gen3):
            self.assertEqual(course_family(member), [root, gen2, gen3])


class VisibilityHelperTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.draft = Assignment.objects.create(
            course=self.m["course"],
            title="Draft",
            slug="draft",
            created_by=self.m["instructor"],
            is_published=False,
        )
        self.draft_problem = Problem.objects.create(assignment=self.draft, points=1)

    def test_accessible_problems_hides_drafts_from_students(self):
        visible = accessible_problems(self.m["student"])
        self.assertIn(self.m["problem"], visible)
        self.assertNotIn(self.draft_problem, visible)

    def test_staff_and_admin_see_draft_problems(self):
        for who in ("instructor", "ta", "admin"):
            self.assertIn(self.draft_problem, accessible_problems(self.m[who]), who)

    def test_editable_helpers_are_instructor_and_admin_only(self):
        for editable in (editable_assignments, editable_problems):
            self.assertNotIn(
                (
                    self.m["problem"].assignment
                    if editable is editable_assignments
                    else self.m["problem"]
                ),
                editable(self.m["ta"]),
            )
            self.assertTrue(editable(self.m["admin"]).exists())  # admin sees everything

    def test_cross_course_isolation(self):
        # A second course where our instructor has no role.
        other = Course.objects.create(title="Other", slug="other-course")
        other_assignment = Assignment.objects.create(
            course=other,
            title="O",
            slug="o1",
            created_by=self.m["admin"],
            is_published=True,
        )
        other_problem = Problem.objects.create(assignment=other_assignment, points=1)
        self.assertNotIn(other_problem, editable_problems(self.m["instructor"]))
        self.assertNotIn(other_assignment, editable_assignments(self.m["instructor"]))

    def test_is_student_anywhere(self):
        self.assertTrue(is_student_anywhere(self.m["student"]))
        self.assertFalse(is_student_anywhere(self.m["outsider"]))


class StrAndHelperCoverageTests(TestCase):
    """`__str__`, role labels, thumbnail resolution, and the small standalone helpers."""

    def setUp(self):
        self.m = make_role_matrix()

    def test_dunder_str_of_each_model(self):
        self.assertEqual(str(self.m["course"]), "Test Course")
        self.assertIn("HW1", str(self.m["assignment"]))
        self.assertIn("P1", str(self.m["problem"]))
        block = self.m["problem"].blocks.first()
        self.assertIn("#", str(block))
        source = LeanSourceFile.objects.create(
            title="Lib", slug="lib", created_by=self.m["instructor"]
        )
        self.assertEqual(str(source), "Lib")
        submission = Submission.objects.create(
            problem=self.m["problem"], user=self.m["student"], code="x"
        )
        self.assertIn("t_student", str(submission))

    def test_role_of_each_role(self):
        course = self.m["course"]
        self.assertEqual(course.role_of(self.m["admin"]), "admin")
        self.assertEqual(course.role_of(self.m["instructor"]), "instructor")
        self.assertEqual(course.role_of(self.m["ta"]), "ta")
        self.assertEqual(course.role_of(self.m["student"]), "student")
        self.assertIsNone(course.role_of(self.m["outsider"]))

    def test_thumbnail_url_prefers_upload_then_preset_then_blank(self):
        self.assertIn("x.png", Course(thumbnail="x.png").thumbnail_url)
        self.assertIn("aurora.svg", Course(thumbnail_preset="aurora.svg").thumbnail_url)
        self.assertEqual(Course().thumbnail_url, "")

    def test_thumbnail_credit(self):
        self.assertIsNone(Course().thumbnail_credit)  # nothing chosen
        self.assertIsNone(
            Course(thumbnail="x.png").thumbnail_credit
        )  # upload, no credit
        credit = Course(thumbnail_preset="aurora.svg").thumbnail_credit
        self.assertIsInstance(credit, dict)  # from the aurora.json sidecar

    def test_position_falls_back_to_one_for_a_detached_problem(self):
        self.assertEqual(Problem(assignment=self.m["assignment"]).position, 1)

    def test_unique_course_slug_suffixes_on_collision(self):
        from apps.homework.models import _unique_course_slug

        self.assertEqual(_unique_course_slug("Test Course"), "test-course-2")

    def test_available_thumbnail_presets_lists_the_static_files(self):
        from apps.homework.models import available_thumbnail_presets

        presets = available_thumbnail_presets()
        self.assertTrue(presets)
        self.assertIn("key", presets[0])

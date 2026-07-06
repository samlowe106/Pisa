"""Scoring policies, per-course/per-student summaries, and the stats-tab plumbing
(apps/homework/reporting.py). The pure statistics in stats.py are covered separately;
here we test how submissions roll up into grades and section samples."""

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.homework.models import Assignment, Course, Problem, Submission
from apps.homework.ops import renew_course
from apps.homework.reporting import (
    _passed_pairs,
    compare_two_sections,
    course_cards_for,
    course_grade_distribution,
    grade_distribution_chart,
    section_score_data,
    staff_course_summary,
    student_course_summary,
)

User = get_user_model()

PASSED = Submission.STATUS_PASSED
FAILED = Submission.STATUS_FAILED


class PassedPairsTests(SimpleTestCase):
    """`_passed_pairs` is pure (operates on (user_id, problem_id, status, created_at) rows)."""

    def test_best_counts_any_passing_attempt(self):
        rows = [(1, 10, PASSED, 1), (1, 10, FAILED, 2)]  # later attempt failed
        self.assertEqual(_passed_pairs(rows, Course.SCORING_BEST), {(1, 10)})

    def test_superscore_matches_best_for_passfail_problems(self):
        rows = [(1, 10, FAILED, 1), (1, 10, PASSED, 2)]
        self.assertEqual(_passed_pairs(rows, Course.SCORING_SUPERSCORE), {(1, 10)})

    def test_recent_uses_only_the_latest_submission(self):
        # Passed first, then failed later -> most-recent policy does NOT count it.
        rows = [(1, 10, PASSED, 1), (1, 10, FAILED, 2)]
        self.assertEqual(_passed_pairs(rows, Course.SCORING_RECENT), set())
        # Failed first, then passed later -> counts.
        rows = [(1, 10, FAILED, 1), (1, 10, PASSED, 2)]
        self.assertEqual(_passed_pairs(rows, Course.SCORING_RECENT), {(1, 10)})

    def test_failing_only_never_counts(self):
        rows = [(1, 10, FAILED, 1), (2, 11, FAILED, 1)]
        for method in (Course.SCORING_BEST, Course.SCORING_RECENT):
            self.assertEqual(_passed_pairs(rows, method), set())


def _build_course(slug="grading", scoring=Course.SCORING_BEST):
    """A course with two published assignments (a1: P1+P2 = 10pts, a2: P3 = 10pts) and three
    students: s1 aces everything (100% -> A), s2 passes only P1 (25% -> F), s3 does nothing.
    """
    instructor = User.objects.create_user(f"{slug}_inst")
    course = Course.objects.create(title="Grading", slug=slug, scoring_method=scoring)
    course.instructors.add(instructor)
    s1 = User.objects.create_user(f"{slug}_s1")
    s2 = User.objects.create_user(f"{slug}_s2")
    s3 = User.objects.create_user(f"{slug}_s3")
    course.students.add(s1, s2, s3)

    a1 = Assignment.objects.create(
        course=course, title="A1", slug="a1", created_by=instructor, is_published=True
    )
    a2 = Assignment.objects.create(
        course=course, title="A2", slug="a2", created_by=instructor, is_published=True
    )
    p1 = Problem.objects.create(assignment=a1, title="P1", points=5, order=0)
    p2 = Problem.objects.create(assignment=a1, title="P2", points=5, order=1)
    p3 = Problem.objects.create(assignment=a2, title="P3", points=10, order=0)

    def passed(user, problem):
        Submission.objects.create(problem=problem, user=user, code="x", status=PASSED)

    for problem in (p1, p2, p3):
        passed(s1, problem)
    passed(s2, p1)
    return {
        "course": course,
        "instructor": instructor,
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "a1": a1,
        "a2": a2,
    }


class StudentSummaryTests(TestCase):
    def setUp(self):
        self.c = _build_course()

    def test_full_marks_student_gets_an_a_and_no_open_assignments(self):
        summary = student_course_summary(self.c["course"], self.c["s1"])
        self.assertEqual(summary["grade"]["letter"], "A")
        self.assertEqual(summary["grade"]["percent"], 100)
        self.assertEqual(summary["grade"]["earned"], 20)
        self.assertEqual(summary["open_assignments"], 0)

    def test_partial_student_grade_and_open_assignments(self):
        summary = student_course_summary(self.c["course"], self.c["s2"])
        self.assertEqual(summary["grade"]["earned"], 5)
        self.assertEqual(summary["grade"]["possible"], 20)
        self.assertEqual(summary["grade"]["letter"], "F")  # 25%
        self.assertEqual(summary["open_assignments"], 2)  # neither fully passed

    def test_inactive_student_is_all_open(self):
        summary = student_course_summary(self.c["course"], self.c["s3"])
        self.assertEqual(summary["grade"]["earned"], 0)
        self.assertEqual(summary["open_assignments"], 2)

    def test_recent_scoring_can_revoke_a_pass(self):
        course = self.c["course"]
        course.scoring_method = Course.SCORING_RECENT
        course.save()
        # s1 re-submits P1 and now fails it -> recent policy drops that 5 points.
        p1 = Problem.objects.get(assignment=self.c["a1"], title="P1")
        Submission.objects.create(
            problem=p1, user=self.c["s1"], code="x", status=FAILED
        )
        summary = student_course_summary(course, self.c["s1"])
        self.assertEqual(summary["grade"]["earned"], 15)  # 20 - 5


class StaffSummaryAndDistributionTests(TestCase):
    def setUp(self):
        self.c = _build_course()

    def test_staff_summary_counts_students_and_drafts(self):
        Assignment.objects.create(
            course=self.c["course"],
            title="Draft",
            slug="draft",
            created_by=self.c["instructor"],
            is_published=False,
        )
        summary = staff_course_summary(self.c["course"])
        self.assertEqual(summary["student_count"], 3)
        self.assertEqual(summary["draft_assignments"], 1)

    def test_grade_distribution_buckets_students(self):
        dist = course_grade_distribution(self.c["course"])
        self.assertEqual(dist["num_students"], 3)
        self.assertEqual(dist["counts"]["A"], 1)  # s1
        self.assertEqual(dist["counts"]["F"], 2)  # s2, s3
        self.assertEqual(dist["ungraded"], 0)

    def test_distribution_marks_ungraded_when_no_points(self):
        empty = Course.objects.create(title="Empty", slug="empty")
        student = User.objects.create_user("empty_s")
        empty.students.add(student)
        dist = course_grade_distribution(empty)
        self.assertEqual(dist["ungraded"], 1)
        self.assertEqual(sum(dist["counts"].values()), 0)


class SectionScoreDataTests(TestCase):
    def setUp(self):
        self.c = _build_course()

    def test_overall_includes_every_enrolled_student_as_percent(self):
        data = section_score_data(self.c["course"])
        self.assertEqual(sorted(data["overall"]), [0.0, 25.0, 100.0])

    def test_per_assignment_only_counts_submitters(self):
        data = section_score_data(self.c["course"])
        # a1 submitters: s1 (100%) and s2 (50%, passed P1 of P1+P2). s3 never submitted.
        self.assertEqual(sorted(data["by_assignment"]["a1"]), [50.0, 100.0])
        # a2 submitter: only s1.
        self.assertEqual(data["by_assignment"]["a2"], [100.0])
        self.assertEqual(data["titles"]["a1"], "A1")


class GradeChartAndCompareTests(TestCase):
    def setUp(self):
        self.c = _build_course()

    def test_grade_distribution_chart_one_section(self):
        sections, chart = grade_distribution_chart(self.c["course"])
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]["counts"]["A"], 1)
        self.assertEqual([row["letter"] for row in chart], ["A", "B", "C", "D", "F"])
        a_row = next(row for row in chart if row["letter"] == "A")
        self.assertEqual(len(a_row["bars"]), 1)  # one bar per section
        self.assertEqual(a_row["bars"][0]["count"], 1)

    def test_compare_two_sections_returns_matched_structure(self):
        # Renew into a second section and give it its own enrolment + a submission so the
        # comparison has something to match on (matched by assignment slug).
        other = renew_course(
            self.c["course"], term="T2", section="", created_by=self.c["instructor"]
        )
        st = User.objects.create_user("other_s1")
        other.students.add(st)
        other_p1 = Problem.objects.get(assignment__course=other, title="P1")
        Submission.objects.create(problem=other_p1, user=st, code="x", status=PASSED)

        counts_a = list(course_grade_distribution(self.c["course"])["counts"].values())
        counts_b = list(course_grade_distribution(other)["counts"].values())
        result = compare_two_sections(self.c["course"], other, counts_a, counts_b)

        self.assertEqual(result["other"], other)
        self.assertIsNotNone(result["overall"])  # a ScoreComparison
        self.assertTrue(hasattr(result["letters"], "_fields") or result["letters"])
        slugs = {row["title"] for row in result["per_assignment"]}
        self.assertTrue(slugs)  # at least one shared assignment compared


class CourseCardsTests(TestCase):
    def test_cards_split_by_active_and_reflect_role(self):
        c = _build_course()
        # An inactive (previous) course the instructor also runs.
        old = Course.objects.create(title="Old", slug="old", is_active=False)
        old.instructors.add(c["instructor"])

        active, previous = course_cards_for(c["instructor"])
        active_slugs = {card["course"].slug for card in active}
        previous_slugs = {card["course"].slug for card in previous}
        self.assertIn("grading", active_slugs)
        self.assertIn("old", previous_slugs)
        # Instructor sees staff cards (roster marker), not a student grade.
        grading_card = next(c for c in active if c["course"].slug == "grading")
        self.assertEqual(grading_card["role"], "staff")
        self.assertEqual(grading_card["student_count"], 3)

    def test_student_sees_a_grade_card(self):
        c = _build_course()
        active, _ = course_cards_for(c["s1"])
        card = next(card for card in active if card["course"].slug == "grading")
        self.assertEqual(card["role"], "student")
        self.assertEqual(card["grade"]["letter"], "A")

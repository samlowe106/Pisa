"""Reporting/analytics for courses: scoring policy resolution, per-student and per-course
summaries, grade distributions, and the stats-tab section comparison.

Everything here is read-only aggregation over ``Submission``/``Problem`` rows. The scoring policy
("latest submission wins" vs "best of") is resolved in exactly one place — ``_passed_pairs`` —
and every summary builds on it.
"""

from collections import defaultdict
from typing import NamedTuple

from django.db.models import Count, Q

from . import stats
from .models import Course, Problem, Submission
from .ops import course_family


def _passed_pairs(submission_rows, scoring_method):
    """Set of (user_id, problem_id) pairs that count as *passed* under the course's policy.

    ``submission_rows`` is an iterable of ``(user_id, problem_id, status, created_at)``.

    - "Most recent submission": only the student's latest submission for the problem counts.
    - "Best attempt" / "Superscored": any passing submission counts. (These two coincide while
      a problem is graded pass/fail as a whole; they would diverge only with per-part credit.)
    """
    if scoring_method == Course.SCORING_RECENT:
        latest: dict[tuple[int, int], tuple] = {}
        for user_id, problem_id, status, created_at in submission_rows:
            key = (user_id, problem_id)
            current = latest.get(key)
            if current is None or created_at > current[0]:
                latest[key] = (created_at, status)
        return {
            key
            for key, (_, status) in latest.items()
            if status == Submission.STATUS_PASSED
        }
    return {
        (user_id, problem_id)
        for user_id, problem_id, status, _ in submission_rows
        if status == Submission.STATUS_PASSED
    }


class EarnedPoints(NamedTuple):
    """Resolved scores for a set of students on a set of problems — see ``earned_points``."""

    rows: list  # raw (user_id, problem_id, status, created_at) submission rows
    passed_pairs: set  # {(user_id, problem_id)} passed under the course scoring policy
    by_user: dict  # {user_id: earned points}; missing user = 0


def earned_points(course, points_by_problem, user_ids):
    """One round trip from (course, ``{problem_id: points}``, roster) to resolved scores.

    Fetches every submission by ``user_ids`` on the given problems and resolves it under
    ``course.scoring_method``. No DB ordering is needed: ``_passed_pairs`` picks the latest
    attempt by comparing ``created_at`` itself.
    """
    rows = list(
        Submission.objects.filter(
            user_id__in=user_ids, problem_id__in=list(points_by_problem)
        ).values_list("user_id", "problem_id", "status", "created_at")
    )
    passed = _passed_pairs(rows, course.scoring_method)
    return EarnedPoints(rows, passed, earned_totals(passed, points_by_problem))


def earned_totals(passed_pairs, points_by_problem):
    """``{user_id: points}`` from passed pairs; pairs outside the points map score 0 (this is
    how the course-detail student table credits only *published* points against passed pairs
    that span drafts)."""
    totals: dict[int, int] = defaultdict(int)
    for user_id, problem_id in passed_pairs:
        if problem_id in points_by_problem:
            totals[user_id] += points_by_problem[problem_id]
    return totals


def earned_by_assignment(passed_pairs, points_by_problem, assignment_of):
    """``{assignment_id: {user_id: earned points}}``. Every ``problem_id`` in ``passed_pairs``
    must appear in ``assignment_of`` (both derive from the same problem fetch)."""
    per: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for user_id, problem_id in passed_pairs:
        per[assignment_of[problem_id]][user_id] += points_by_problem.get(problem_id, 0)
    return per


def submitters_by_assignment(rows, assignment_of):
    """``{assignment_id: {user_id, ...}}`` — distinct users with any submission per assignment."""
    submitters: dict[int, set] = defaultdict(set)
    for user_id, problem_id, _status, _created in rows:
        submitters[assignment_of[problem_id]].add(user_id)
    return submitters


def student_course_summary(course, student):
    """A student's standing in a course: their letter grade plus the count of *open*
    assignments (published assignments where they haven't passed every problem).

    Returns ``{"grade": {letter, cls, earned, possible, percent} | None, "open_assignments": int}``.
    """
    problem_rows = list(
        Problem.objects.filter(
            assignment__course=course, assignment__is_published=True
        ).values_list("id", "points", "assignment_id")
    )
    points_by_problem = {pid: points for pid, points, _ in problem_rows}
    problems_by_assignment = defaultdict(list)
    for pid, _, assignment_id in problem_rows:
        problems_by_assignment[assignment_id].append(pid)

    ep = earned_points(course, points_by_problem, [student.pk])
    passed = {pid for _, pid in ep.passed_pairs}

    open_assignments = sum(
        1
        for pids in problems_by_assignment.values()
        if not all(pid in passed for pid in pids)
    )

    grade = None
    total = sum(points_by_problem.values())
    if total:
        earned = ep.by_user.get(student.pk, 0)
        percent = earned / total * 100
        letter, css_class = course.letter_for(percent)
        grade = {
            "letter": letter,
            "cls": css_class,
            "earned": earned,
            "possible": total,
            "percent": percent,
        }
    return {"grade": grade, "open_assignments": open_assignments}


def staff_course_summary(course):
    """At-a-glance figures for a course the viewer runs: how many students are enrolled
    and how many assignments are still unpublished drafts (needing the teacher's action).

    Reads the ``_student_count`` / ``_draft_count`` annotations when ``course`` carries them
    (``course_cards_for`` adds them in one query); falls back to per-course counts otherwise.
    """
    student_count = getattr(course, "_student_count", None)
    if student_count is None:
        student_count = course.students.count()
    draft_count = getattr(course, "_draft_count", None)
    if draft_count is None:
        draft_count = course.assignments.filter(is_published=False).count()
    return {"student_count": student_count, "draft_assignments": draft_count}


def course_cards_for(user):
    """Build ``(active_cards, previous_cards)`` for the course-list landing page.

    Each card reflects the user's role *in that course*: course staff get a roster-size
    marker and a draft-assignment badge; students get their letter grade and an open-
    assignment count. Courses are split by ``is_active`` ("previous" = inactive). Site
    admins see every course (for oversight); everyone else sees the courses they're in.
    """
    if user.is_staff:
        courses = Course.objects.all()
    else:
        courses = Course.objects.filter(
            Q(instructors=user) | Q(tas=user) | Q(students=user)
        ).distinct()
    # Roster size + draft count for the staff cards, computed in the list query (not N queries).
    courses = courses.annotate(
        _student_count=Count("students", distinct=True),
        _draft_count=Count(
            "assignments",
            filter=Q(assignments__is_published=False),
            distinct=True,
        ),
    ).order_by("-created_at")
    active, previous = [], []
    for course in courses:
        if course.is_course_staff(user):
            card = {"course": course, "role": "staff", **staff_course_summary(course)}
        else:
            card = {
                "course": course,
                "role": "student",
                **student_course_summary(course, user),
            }
        (active if course.is_active else previous).append(card)
    return active, previous


_GRADE_LETTERS = ["A", "B", "C", "D", "F"]


def course_grade_distribution(course):
    """How many of ``course``'s enrolled students land in each letter band, plus how many have
    no graded work yet (the course has no published problems with points)."""
    enrolled_ids = list(course.students.values_list("id", flat=True))
    points_by_problem = dict(
        Problem.objects.filter(
            assignment__course=course, assignment__is_published=True
        ).values_list("id", "points")
    )
    total = sum(points_by_problem.values())
    earned = earned_points(course, points_by_problem, enrolled_ids).by_user

    counts = {letter: 0 for letter in _GRADE_LETTERS}
    ungraded = 0
    for user_id in enrolled_ids:
        if not total:
            ungraded += 1
            continue
        letter, _ = course.letter_for(earned.get(user_id, 0) / total * 100)
        counts[letter] += 1
    return {"counts": counts, "ungraded": ungraded, "num_students": len(enrolled_ids)}


def grade_distribution_chart(course):
    """Stats-tab data: each offering (section) in ``course``'s family with its A–F counts, and
    a per-letter list of colour-coded bars scaled to the largest count for rendering."""
    sections = []
    max_count = 1
    for index, offering in enumerate(course_family(course)):
        distribution = course_grade_distribution(offering)
        sections.append(
            {
                "course": offering,
                "color_class": f"section-color-{index % 6}",
                "counts": distribution["counts"],
                "ungraded": distribution["ungraded"],
                "num_students": distribution["num_students"],
            }
        )
        max_count = max(max_count, max(distribution["counts"].values(), default=0))
    chart = [
        {
            "letter": letter,
            "bars": [
                {
                    "color_class": section["color_class"],
                    "count": section["counts"][letter],
                    "height": section["counts"][letter] / max_count * 100,
                    "label": section["course"].display_name,
                }
                for section in sections
            ],
        }
        for letter in _GRADE_LETTERS
    ]
    return sections, chart


def section_score_data(course):
    """Per-section samples for the hypothesis tests: every enrolled student's overall course
    percent, and each *submitter's* percent on each assignment (keyed by assignment slug). Also
    returns a slug → title map for display."""
    enrolled_ids = list(course.students.values_list("id", flat=True))
    problem_rows = list(
        Problem.objects.filter(
            assignment__course=course, assignment__is_published=True
        ).values_list(
            "id", "points", "assignment_id", "assignment__slug", "assignment__title"
        )
    )
    points_by_problem = {pid: pts for pid, pts, _, _, _ in problem_rows}
    problem_assignment = {pid: aid for pid, _, aid, _, _ in problem_rows}
    assignment_points: dict[int, int] = defaultdict(int)
    slug_of: dict[int, str] = {}
    titles: dict[str, str] = {}
    for _pid, pts, aid, slug, title in problem_rows:
        assignment_points[aid] += pts
        slug_of[aid] = slug
        titles[slug] = title
    total = sum(points_by_problem.values())

    ep = earned_points(course, points_by_problem, enrolled_ids)
    overall_earned = ep.by_user
    earned_per_assignment = earned_by_assignment(
        ep.passed_pairs, points_by_problem, problem_assignment
    )
    submitters = submitters_by_assignment(ep.rows, problem_assignment)

    # Overall: every enrolled student (non-submitters count as 0 — it's their course grade).
    overall = (
        [overall_earned.get(uid, 0) / total * 100 for uid in enrolled_ids]
        if total
        else []
    )
    # Per assignment: only students who submitted something (selection caveat noted in the UI).
    by_assignment = {}
    for aid, slug in slug_of.items():
        assignment_total = assignment_points.get(aid, 0)
        by_assignment[slug] = (
            [
                earned_per_assignment[aid].get(uid, 0) / assignment_total * 100
                for uid in submitters.get(aid, set())
            ]
            if assignment_total
            else []
        )
    return {"overall": overall, "by_assignment": by_assignment, "titles": titles}


def compare_two_sections(course_a, course_b, counts_a, counts_b):
    """Full comparison of two sections — overall course grades, the letter-grade mix, and each
    shared assignment (matched by slug) — with Benjamini–Hochberg-corrected per-assignment
    p-values. ``counts_*`` are A–F count vectors. Observational, not causal."""
    data_a = section_score_data(course_a)
    data_b = section_score_data(course_b)

    per_assignment = []
    for slug, scores_a in data_a["by_assignment"].items():
        if slug not in data_b["by_assignment"]:
            continue
        per_assignment.append(
            {
                "title": data_a["titles"].get(slug, slug),
                "scores": stats.compare_scores(scores_a, data_b["by_assignment"][slug]),
            }
        )
    comparable = [
        i for i, row in enumerate(per_assignment) if row["scores"].welch is not None
    ]
    adjusted = stats.false_discovery_control(
        [per_assignment[i]["scores"].welch.pvalue for i in comparable]
    )
    for i, adj in zip(comparable, adjusted):
        per_assignment[i]["adj_pvalue"] = adj

    return {
        "other": course_b,
        "overall": stats.compare_scores(data_a["overall"], data_b["overall"]),
        "letters": stats.compare_letters(counts_a, counts_b),
        "per_assignment": per_assignment,
    }

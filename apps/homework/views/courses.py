from collections import Counter, defaultdict

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    TemplateView,
    UpdateView,
    View,
)

from .. import stats
from ..forms import (
    CourseForm,
    CourseRenewForm,
)
from ..models import (
    Course,
    Problem,
    Submission,
    course_family,
    editable_courses,
    renew_course,
)
from ..utils import display_name
from .mixins import StaffRequiredMixin

# POST `role` value -> the Course M2M it maps to.
ROLE_RELATIONS = {"instructor": "instructors", "ta": "tas", "student": "students"}


class CourseListView(LoginRequiredMixin, TemplateView):
    """The app's landing page: a card view of the viewer's courses — staff and students
    alike — split into active and previous."""

    template_name = "homework/course_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        active, previous = course_cards_for(self.request.user)
        context["active_cards"] = active
        context["previous_cards"] = previous
        # A grades toggle is only meaningful when some card carries a grade (the viewer is
        # a student somewhere); a pure teacher has nothing to hide.
        context["show_grades_toggle"] = any(
            card.get("grade") for card in active + previous
        )
        return context


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

    rows = Submission.objects.filter(
        user=student, problem_id__in=list(points_by_problem)
    ).values_list("user_id", "problem_id", "status", "created_at")
    passed = {pid for _, pid in _passed_pairs(rows, course.scoring_method)}

    open_assignments = sum(
        1
        for pids in problems_by_assignment.values()
        if not all(pid in passed for pid in pids)
    )

    grade = None
    total = sum(points_by_problem.values())
    if total:
        earned = sum(
            points_by_problem[pid] for pid in passed if pid in points_by_problem
        )
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
    rows = Submission.objects.filter(
        user_id__in=enrolled_ids, problem_id__in=list(points_by_problem)
    ).values_list("user_id", "problem_id", "status", "created_at")
    earned: dict[int, int] = defaultdict(int)
    for user_id, problem_id in _passed_pairs(rows, course.scoring_method):
        earned[user_id] += points_by_problem.get(problem_id, 0)

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

    rows = list(
        Submission.objects.filter(
            user_id__in=enrolled_ids, problem_id__in=list(points_by_problem)
        ).values_list("user_id", "problem_id", "status", "created_at")
    )
    passed = _passed_pairs(rows, course.scoring_method)

    overall_earned: dict[int, int] = defaultdict(int)
    earned_per_assignment: dict[int, dict[int, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for user_id, problem_id in passed:
        points = points_by_problem.get(problem_id, 0)
        overall_earned[user_id] += points
        earned_per_assignment[problem_assignment[problem_id]][user_id] += points

    submitters: dict[int, set] = defaultdict(set)
    for user_id, problem_id, _status, _created in rows:
        submitters[problem_assignment[problem_id]].add(user_id)

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


class CourseDetailView(LoginRequiredMixin, DetailView):
    model = Course
    template_name = "homework/course_detail.html"
    context_object_name = "course"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return Course.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = self.object
        user = self.request.user
        is_course_staff = course.is_course_staff(user)

        if is_course_staff:
            assignments = list(course.assignments.order_by("-created_at"))
        else:
            assignments = list(
                course.assignments.filter(is_published=True).order_by("-created_at")
            )

        enrolled_ids = list(course.students.values_list("id", flat=True))
        num_students = len(enrolled_ids)
        problems = list(
            Problem.objects.filter(assignment__in=assignments).order_by(
                "assignment_id", "order", "created_at"
            )
        )

        # Resolve each (student, problem) to passed/not under the course scoring policy, then
        # reuse that for both the per-problem class average and the per-student grade.
        submission_rows = Submission.objects.filter(
            user_id__in=enrolled_ids,
            problem_id__in=[problem.id for problem in problems],
        ).values_list("user_id", "problem_id", "status", "created_at")
        passed_pairs = _passed_pairs(submission_rows, course.scoring_method)
        passes_per_problem = Counter(problem_id for _, problem_id in passed_pairs)

        problems_by_assignment = defaultdict(list)
        for problem in problems:
            problems_by_assignment[problem.assignment_id].append(problem)

        assignment_rows = []
        for assignment in assignments:
            rows = []
            for number, problem in enumerate(
                problems_by_assignment.get(assignment.id, []), start=1
            ):
                passed = passes_per_problem.get(problem.id, 0)
                rows.append(
                    {
                        "number": number,
                        "problem": problem,
                        "passed": passed,
                        "mean_percent": (
                            passed / num_students * 100 if num_students else None
                        ),
                        "mean_points": (
                            passed / num_students * problem.points
                            if num_students
                            else None
                        ),
                        "url": reverse(
                            "homework:problem_detail",
                            kwargs={
                                "course_slug": course.slug,
                                "assignment_slug": assignment.slug,
                                "number": number,
                            },
                        ),
                    }
                )
            assignment_rows.append({"assignment": assignment, "problems": rows})

        context["assignment_rows"] = assignment_rows
        context["num_students"] = num_students
        context["scoring_method_label"] = course.get_scoring_method_display()
        context["is_course_staff"] = is_course_staff
        context["can_edit"] = course.is_instructor(user)
        context["can_manage_instructors"] = course.can_manage_instructors(user)
        # Offering lineage (instructors + admins): the offering this was renewed from and the
        # offerings renewed from it.
        if context["can_edit"]:
            context["renewed_from"] = course.renewed_from
            context["renewals"] = list(course.renewals.order_by("-created_at"))
        context["instructors"] = [
            {"id": u.id, "name": display_name(u)} for u in course.instructors.all()
        ]
        context["tas"] = [
            {"id": u.id, "name": display_name(u)} for u in course.tas.all()
        ]

        # Per-student course grade is staff-only (students must not see each other's grades).
        students_qs = course.students.order_by("last_name", "first_name", "username")
        if is_course_staff:
            published_assignment_ids = {a.id for a in assignments if a.is_published}
            points_by_problem = {
                problem.id: problem.points
                for problem in problems
                if problem.assignment_id in published_assignment_ids
            }
            total_possible = sum(points_by_problem.values())
            earned_by_user: dict[int, int] = defaultdict(int)
            for user_id, problem_id in passed_pairs:
                if problem_id in points_by_problem:
                    earned_by_user[user_id] += points_by_problem[problem_id]
            students = [
                {
                    "id": student.id,
                    "name": display_name(student),
                    "earned": earned_by_user.get(student.id, 0),
                    "possible": total_possible,
                    "percent": (
                        earned_by_user.get(student.id, 0) / total_possible * 100
                        if total_possible
                        else None
                    ),
                }
                for student in students_qs
            ]
        else:
            students = [
                {"id": student.id, "name": display_name(student)}
                for student in students_qs
            ]
        context["students"] = students

        # --- Statistics tab (staff only) ---
        if is_course_staff:
            problem_to_assignment = {p.id: p.assignment_id for p in problems}
            points_by_problem_all = {p.id: p.points for p in problems}
            assignment_total_points: dict[int, int] = defaultdict(int)
            for problem in problems:
                assignment_total_points[problem.assignment_id] += problem.points

            # Distinct students who submitted anything to each assignment.
            submitters_by_assignment: dict[int, set] = defaultdict(set)
            for user_id, problem_id, _status, _created in submission_rows:
                assignment_id = problem_to_assignment.get(problem_id)
                if assignment_id is not None:
                    submitters_by_assignment[assignment_id].add(user_id)

            # Points each submitter earned per assignment (under the scoring policy).
            earned_per_assignment: dict[int, dict[int, int]] = defaultdict(
                lambda: defaultdict(int)
            )
            for user_id, problem_id in passed_pairs:
                assignment_id = problem_to_assignment.get(problem_id)
                if assignment_id is not None:
                    earned_per_assignment[assignment_id][
                        user_id
                    ] += points_by_problem_all.get(problem_id, 0)

            assignment_stats = []
            for assignment in assignments:
                submitters = submitters_by_assignment.get(assignment.id, set())
                total_points = assignment_total_points.get(assignment.id, 0)
                if submitters and total_points:
                    mean_score = sum(
                        earned_per_assignment[assignment.id].get(uid, 0)
                        / total_points
                        * 100
                        for uid in submitters
                    ) / len(submitters)
                else:
                    mean_score = None  # nobody submitted (or nothing to score)
                assignment_stats.append(
                    {
                        "assignment": assignment,
                        "submitters": len(submitters),
                        "num_students": num_students,
                        "mean_score": mean_score,
                    }
                )
            context["assignment_stats"] = assignment_stats

            grade_sections, grade_chart = grade_distribution_chart(course)
            context["grade_sections"] = grade_sections
            context["grade_chart"] = grade_chart

            # Section comparison is computed only when explicitly requested (?cmp=<slug>), so a
            # normal page load doesn't pay for the permutation tests.
            others = [
                section["course"]
                for section in grade_sections
                if section["course"].pk != course.pk
            ]
            context["compare_options"] = others
            requested = self.request.GET.get("cmp")
            if requested:
                other = next((c for c in others if c.slug == requested), None)
                if other is not None:
                    counts = {s["course"].pk: s["counts"] for s in grade_sections}
                    context["comparison"] = compare_two_sections(
                        course,
                        other,
                        [counts[course.pk][letter] for letter in _GRADE_LETTERS],
                        [counts[other.pk][letter] for letter in _GRADE_LETTERS],
                    )
                    context["compare_active"] = other.slug

        return context


class CourseCreateView(LoginRequiredMixin, StaffRequiredMixin, CreateView):
    model = Course
    form_class = CourseForm
    template_name = "homework/course_form.html"
    success_url = reverse_lazy("homework:course_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        form.apply_rosters(self.object)
        # The admin who creates a course starts as one of its instructors.
        self.object.instructors.add(self.request.user)
        return response


class CourseUpdateView(LoginRequiredMixin, UpdateView):
    model = Course
    form_class = CourseForm
    template_name = "homework/course_form.html"

    def get_queryset(self):
        return editable_courses(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        form.apply_rosters(self.object)
        return response


class CourseRenewView(LoginRequiredMixin, View):
    """Clone a course into a new term/section offering. Instructors and admins only."""

    template_name = "homework/course_renew.html"

    def _course(self):
        return get_object_or_404(
            editable_courses(self.request.user), slug=self.kwargs["slug"]
        )

    def get(self, request, slug):
        return render(
            request,
            self.template_name,
            {"course": self._course(), "form": CourseRenewForm()},
        )

    def post(self, request, slug):
        course = self._course()
        form = CourseRenewForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"course": course, "form": form})
        with transaction.atomic():
            new_course = renew_course(
                course,
                term=form.cleaned_data["term"],
                section=form.cleaned_data["section"],
                created_by=request.user,
            )
        messages.success(
            request, f"Renewed “{course.title}” as {new_course.display_name}."
        )
        return redirect("homework:course_detail", slug=new_course.slug)


class CourseEnrollView(LoginRequiredMixin, View):
    def post(self, request, slug):
        course = get_object_or_404(Course, slug=slug)
        if course.is_course_staff(request.user):
            return HttpResponseBadRequest("Course staff cannot enrol as students.")
        course.students.add(request.user)
        return redirect("homework:course_detail", slug=slug)


def _can_manage_role(course, user, role):
    """Owner of the action: only admins manage instructors; instructors manage TAs/students."""
    if role == "instructor":
        return course.can_manage_instructors(user)
    return course.is_instructor(user)


class CourseAddMemberView(LoginRequiredMixin, View):
    def post(self, request, slug):
        course = get_object_or_404(Course, slug=slug)
        role = request.POST.get("role")
        if role not in ROLE_RELATIONS:
            return HttpResponseBadRequest("Unknown role.")
        if not _can_manage_role(course, request.user, role):
            raise PermissionDenied
        identifier = request.POST.get("identifier", "").strip()
        user_model = get_user_model()
        member = (
            user_model.objects.filter(username=identifier).first()
            or user_model.objects.filter(email__iexact=identifier).first()
        )
        if member is None:
            messages.error(request, f"No user found matching “{identifier}”.")
            return redirect("homework:course_detail", slug=slug)
        # Roles are exclusive within a course.
        course.instructors.remove(member)
        course.tas.remove(member)
        course.students.remove(member)
        getattr(course, ROLE_RELATIONS[role]).add(member)
        messages.success(request, f"Added {display_name(member)} as {role}.")
        return redirect("homework:course_detail", slug=slug)


class CourseRemoveMemberView(LoginRequiredMixin, View):
    def post(self, request, slug):
        course = get_object_or_404(Course, slug=slug)
        role = request.POST.get("role")
        if role not in ROLE_RELATIONS:
            return HttpResponseBadRequest("Unknown role.")
        if not _can_manage_role(course, request.user, role):
            raise PermissionDenied
        member = get_object_or_404(get_user_model(), pk=request.POST.get("user_id"))
        getattr(course, ROLE_RELATIONS[role]).remove(member)
        messages.success(request, f"Removed {display_name(member)}.")
        return redirect("homework:course_detail", slug=slug)

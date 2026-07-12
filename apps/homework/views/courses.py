from collections import Counter, defaultdict

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
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

from ..forms import (
    CourseForm,
    CourseRenewForm,
)
from ..models import Course, Problem
from ..ops import renew_course
from ..reporting import (
    _GRADE_LETTERS,
    compare_two_sections,
    course_cards_for,
    earned_by_assignment,
    earned_points,
    earned_totals,
    grade_distribution_chart,
    submitters_by_assignment,
)
from ..selectors import editable_courses
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
        # reuse that for the per-problem class average, the per-student grade, and the stats
        # tab. Passed pairs span drafts here (staff see them); published-only totals are
        # re-derived below via earned_totals with a narrower points map.
        points_by_problem_all = {problem.id: problem.points for problem in problems}
        problem_to_assignment = {
            problem.id: problem.assignment_id for problem in problems
        }
        ep = earned_points(course, points_by_problem_all, enrolled_ids)
        passes_per_problem = Counter(problem_id for _, problem_id in ep.passed_pairs)

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
            earned_by_user = earned_totals(ep.passed_pairs, points_by_problem)
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

        # region Statistics tab (staff only)
        if is_course_staff:
            assignment_total_points: dict[int, int] = defaultdict(int)
            for problem in problems:
                assignment_total_points[problem.assignment_id] += problem.points

            # Distinct students who submitted anything to each assignment, and the points each
            # submitter earned per assignment (under the scoring policy).
            submitters_per_assignment = submitters_by_assignment(
                ep.rows, problem_to_assignment
            )
            earned_per_assignment = earned_by_assignment(
                ep.passed_pairs, points_by_problem_all, problem_to_assignment
            )

            assignment_stats = []
            for assignment in assignments:
                submitters = submitters_per_assignment.get(assignment.id, set())
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
        # endregion

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

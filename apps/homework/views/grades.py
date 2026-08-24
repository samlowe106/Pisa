from typing import TYPE_CHECKING

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView, View

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.models import QuerySet
    from django.http import HttpResponse

from ..exports import export_submissions_csv, export_submissions_excel
from ..models import (
    Assignment,
    Course,
    Submission,
)
from ..selectors import _staff_course_ids


def _visible_grades(user) -> tuple[QuerySet, QuerySet | None, bool]:
    """Submissions/assignments visible to ``user`` for grading, and whether they're viewing
    as course staff (staff/admin, sees every submission in scope) rather than as a student
    with no staff role anywhere (sees only their own submissions, no assignment list).
    """
    if user.is_staff:
        return Submission.objects.all(), Assignment.objects.all(), True
    staff_course_ids = _staff_course_ids(user)
    if staff_course_ids:
        submissions = Submission.objects.filter(
            problem__assignment__course_id__in=staff_course_ids
        )
        assignments = Assignment.objects.filter(course_id__in=staff_course_ids)
        return submissions, assignments, True
    return Submission.objects.filter(user=user), None, False


class GradesView(LoginRequiredMixin, TemplateView):
    """Grade overview: course staff/admins see every submission in scope; a student with no
    staff role anywhere sees only their own submissions."""

    template_name = "homework/grades.html"

    def get_context_data(self, **kwargs) -> dict:
        context = super().get_context_data(**kwargs)
        submissions, assignments, is_staff_view = _visible_grades(self.request.user)
        context["is_staff_view"] = is_staff_view
        if is_staff_view:
            context["submissions"] = submissions.select_related(
                "problem", "problem__assignment", "problem__assignment__course", "user"
            ).order_by("-created_at")
            context["assignments"] = assignments.prefetch_related(
                "problems__submissions"
            ).order_by("-created_at")
        else:
            context["submissions"] = submissions.select_related(
                "problem", "problem__assignment"
            ).order_by("-created_at")
        return context


class BaseExportGradesView(LoginRequiredMixin, View):
    """Export a course's grades (course instructors and site admins only, not TAs).

    Subclasses set ``export_func`` to the exports.py function for their format.
    """

    export_func: Callable | None = None  # staticmethod(export_submissions_*)

    def get(self, request, course_slug: str) -> HttpResponse:
        course = get_object_or_404(Course, slug=course_slug)
        if not course.is_instructor(request.user):
            raise PermissionDenied
        submissions = Submission.objects.filter(
            problem__assignment__course=course
        ).order_by("-created_at")
        return self.export_func(submissions)


class ExportGradesCSVView(BaseExportGradesView):
    export_func = staticmethod(export_submissions_csv)


class ExportGradesExcelView(BaseExportGradesView):
    export_func = staticmethod(export_submissions_excel)

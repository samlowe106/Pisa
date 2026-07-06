from collections.abc import Callable

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView, View

from ..exports import export_submissions_csv, export_submissions_excel
from ..models import (
    Assignment,
    Course,
    Submission,
)
from ..selectors import _staff_course_ids


class GradesView(LoginRequiredMixin, TemplateView):
    template_name = "homework/grades.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        if user.is_staff:
            # Site admins see every course's grades.
            submissions = Submission.objects.all()
            assignments = Assignment.objects.all()
            is_staff_view = True
        else:
            staff_course_ids = _staff_course_ids(user)
            is_staff_view = bool(staff_course_ids)
            if is_staff_view:
                submissions = Submission.objects.filter(
                    problem__assignment__course_id__in=staff_course_ids
                )
                assignments = Assignment.objects.filter(course_id__in=staff_course_ids)
            else:
                submissions = Submission.objects.filter(user=user)
                assignments = None

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
    """Export a course's grades (course instructors and site admins only — not TAs).

    Subclasses set ``export_func`` to the exports.py function for their format.
    """

    export_func: Callable | None = None  # staticmethod(export_submissions_*)

    def get(self, request, course_slug):
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

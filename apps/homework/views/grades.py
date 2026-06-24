from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView, View

from ..models import (
    Assignment,
    Course,
    Submission,
)


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
            staff_course_ids = list(
                Course.objects.filter(Q(instructors=user) | Q(tas=user)).values_list(
                    "id", flat=True
                )
            )
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


class ExportGradesCSVView(LoginRequiredMixin, View):
    """Export course grades as CSV (course instructors and site admins only — not TAs)."""

    def get(self, request, course_slug):
        from ..exports import export_submissions_csv

        course = get_object_or_404(Course, slug=course_slug)
        if not course.is_instructor(request.user):
            raise PermissionDenied
        submissions = Submission.objects.filter(
            problem__assignment__course=course
        ).order_by("-created_at")
        return export_submissions_csv(submissions)


class ExportGradesExcelView(LoginRequiredMixin, View):
    """Export course grades as Excel (course instructors and site admins only — not TAs)."""

    def get(self, request, course_slug):
        from ..exports import export_submissions_excel

        course = get_object_or_404(Course, slug=course_slug)
        if not course.is_instructor(request.user):
            raise PermissionDenied
        submissions = Submission.objects.filter(
            problem__assignment__course=course
        ).order_by("-created_at")
        return export_submissions_excel(submissions)

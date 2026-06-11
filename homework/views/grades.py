from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
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
        if self.request.user.is_staff:
            courses = self.request.user.courses_taught.all()
            context["submissions"] = (
                Submission.objects.filter(problem__assignment__course__in=courses)
                .select_related("problem", "problem__assignment", "user")
                .order_by("-created_at")
            )
            context["assignments"] = (
                Assignment.objects.filter(course__in=courses)
                .prefetch_related("problems__submissions")
                .order_by("-created_at")
            )
        else:
            context["submissions"] = (
                Submission.objects.filter(user=self.request.user)
                .select_related("problem", "problem__assignment")
                .order_by("-created_at")
            )
        return context


class ExportGradesCSVView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Export course grades as CSV for instructors."""

    def test_func(self):
        return self.request.user.is_staff

    def get(self, request, course_slug):
        from .exports import export_submissions_csv

        course = get_object_or_404(Course, slug=course_slug, instructor=request.user)
        submissions = Submission.objects.filter(
            problem__assignment__course=course
        ).order_by("-created_at")

        return export_submissions_csv(submissions)


class ExportGradesExcelView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Export course grades as Excel for instructors."""

    def test_func(self):
        return self.request.user.is_staff

    def get(self, request, course_slug):
        from .exports import export_submissions_excel

        course = get_object_or_404(Course, slug=course_slug, instructor=request.user)
        submissions = Submission.objects.filter(
            problem__assignment__course=course
        ).order_by("-created_at")

        return export_submissions_excel(submissions)

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404
from django.views.generic import View

from ..models import (
    Course,
    Submission,
)


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

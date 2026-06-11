import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.forms import inlineformset_factory
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from ..forms import (
    AssignmentForm,
    CourseForm,
    LeanSourceFileForm,
    ProblemBlockForm,
    ProblemForm,
)
from ..models import (
    Assignment,
    Course,
    LeanSourceFile,
    Problem,
    ProblemBlock,
    Submission,
)
from .assignments import (
    AssignmentCreateView,
    AssignmentDetailView,
    AssignmentListView,
    AssignmentUpdateView,
    ProblemCreateView,
    ProblemDetailView,
    ProblemRunView,
    ProblemSubmitView,
    ProblemUpdateView,
)
from .courses import (
    CourseCreateView,
    CourseDetailView,
    CourseEnrollView,
    CourseListView,
    CourseUpdateView,
)
from .export_grades import ExportGradesCSVView, ExportGradesExcelView
from .lean_source_files import (
    LeanSourceFileCreateView,
    LeanSourceFileListView,
    LeanSourceFileUpdateView,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "homework/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_staff:
            context["courses"] = self.request.user.courses_taught.all()
            context["assignments"] = Assignment.objects.filter(
                course__in=self.request.user.courses_taught.all()
            ).order_by("-created_at")
        else:
            context["courses"] = self.request.user.courses_enrolled.all()
            context["assignments"] = Assignment.objects.filter(
                course__students=self.request.user,
                is_published=True,
            ).order_by("-created_at")
        return context


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

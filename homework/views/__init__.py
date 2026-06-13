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
from django.views.generic import TemplateView

from .assignments import (
    AssignmentCreateView,
    AssignmentDetailView,
    AssignmentListView,
    AssignmentUpdateView,
)
from .courses import (
    CourseCreateView,
    CourseDetailView,
    CourseEnrollView,
    CourseListView,
    CourseUpdateView,
)
from .dashboard import DashboardView
from .grades import ExportGradesCSVView, ExportGradesExcelView, GradesView
from .lean_source_files import (
    LeanSourceFileCreateView,
    LeanSourceFileListView,
    LeanSourceFileUpdateView,
)
from .problems import (
    ProblemCreateView,
    ProblemDetailView,
    ProblemRunView,
    ProblemSubmitView,
    ProblemUpdateView,
)

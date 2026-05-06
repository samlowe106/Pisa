import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.forms import inlineformset_factory
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
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

from .forms import AssignmentForm, ProblemForm
from .models import Assignment, Problem, Submission

ProblemFormSet = inlineformset_factory(
    Assignment,
    Problem,
    form=ProblemForm,
    extra=1,
    can_delete=True,
    fields=[
        "title",
        "statement",
        "starter_code",
        "required_code",
        "grading_stub",
        "order",
    ],
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "homework/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignments"] = Assignment.objects.filter(is_published=True).order_by(
            "-created_at"
        )
        return context


class GradesView(LoginRequiredMixin, TemplateView):
    template_name = "homework/grades.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_staff:
            # Instructors see all submissions
            context["submissions"] = Submission.objects.select_related(
                "problem", "problem__assignment", "user"
            ).order_by("-created_at")
            context["assignments"] = Assignment.objects.prefetch_related(
                "problems__submissions"
            ).order_by("-created_at")
        else:
            # Students see only their submissions
            context["submissions"] = (
                Submission.objects.filter(user=self.request.user)
                .select_related("problem", "problem__assignment")
                .order_by("-created_at")
            )
        return context


class AssignmentListView(LoginRequiredMixin, ListView):
    model = Assignment
    template_name = "homework/assignment_list.html"
    context_object_name = "assignments"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Assignment.objects.all().order_by("-created_at")
        return Assignment.objects.filter(is_published=True).order_by("-created_at")


class AssignmentDetailView(LoginRequiredMixin, DetailView):
    model = Assignment
    template_name = "homework/assignment_detail.html"
    context_object_name = "assignment"

    def get_queryset(self):
        return Assignment.objects.filter(is_published=True)


class AssignmentCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"
    success_url = reverse_lazy("homework:dashboard")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context["problem_formset"] = ProblemFormSet(self.request.POST)
        else:
            context["problem_formset"] = ProblemFormSet()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        problem_formset = context["problem_formset"]
        if problem_formset.is_valid():
            form.instance.created_by = self.request.user
            self.object = form.save()
            problem_formset.instance = self.object
            problem_formset.save()
            return super().form_valid(form)
        else:
            return self.form_invalid(form)

    def test_func(self):
        return self.request.user.is_staff


class AssignmentUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context["problem_formset"] = ProblemFormSet(
                self.request.POST, instance=self.object
            )
        else:
            context["problem_formset"] = ProblemFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        problem_formset = context["problem_formset"]
        if problem_formset.is_valid():
            self.object = form.save()
            problem_formset.instance = self.object
            problem_formset.save()
            return super().form_valid(form)
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return reverse_lazy(
            "homework:assignment_detail", kwargs={"slug": self.object.slug}
        )

    def test_func(self):
        return self.request.user.is_staff


class ProblemCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Problem
    form_class = ProblemForm
    template_name = "homework/problem_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.assignment = get_object_or_404(Assignment, slug=kwargs["assignment_slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.assignment
        return context

    def form_valid(self, form):
        form.instance.assignment = self.assignment
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "homework:assignment_detail", kwargs={"slug": self.assignment.slug}
        )

    def test_func(self):
        return self.request.user.is_staff


class ProblemUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Problem
    form_class = ProblemForm
    template_name = "homework/problem_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.object.assignment
        return context

    def get_success_url(self):
        return reverse_lazy("homework:problem_detail", kwargs={"pk": self.object.pk})

    def test_func(self):
        return self.request.user.is_staff


class ProblemDetailView(LoginRequiredMixin, DetailView):
    model = Problem
    template_name = "homework/problem_detail.html"
    context_object_name = "problem"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context["submissions"] = self.object.submissions.filter(
                user=self.request.user
            )
        else:
            context["submissions"] = []
        return context


def get_lean_executable() -> str:
    explicit = getattr(settings, "LEAN_EXECUTABLE", None)
    if explicit:
        return explicit

    found = shutil.which("lean")
    if found:
        return found

    elan_path = Path.home() / ".elan" / "bin" / "lean"
    if elan_path.exists():
        return str(elan_path)

    raise FileNotFoundError("Lean executable not found")


def grade_lean_submission(problem: Problem, code: str) -> tuple[str, str]:
    if problem.required_code and problem.required_code.strip() not in code:
        return (
            Submission.STATUS_FAILED,
            "Your submission does not include the required code snippet.",
        )

    source_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w+", suffix=".lean", delete=False
        ) as source_file:
            source_file.write(code)
            if problem.grading_stub:
                source_file.write("\n\n")
                source_file.write(problem.grading_stub)
            source_path = Path(source_file.name)

        command = [get_lean_executable(), str(source_path)]
        result = subprocess.run(
            command,
            cwd=source_path.parent,
            capture_output=True,
            text=True,
            timeout=12,
        )
        if result.returncode == 0:
            return (
                Submission.STATUS_PASSED,
                result.stdout or "Lean compiled successfully.",
            )
        return (
            Submission.STATUS_FAILED,
            result.stderr
            or result.stdout
            or f"Lean exited with code {result.returncode}",
        )
    except FileNotFoundError:
        return (
            Submission.STATUS_ERROR,
            "Lean executable not found. Install Lean on the server or configure a Lean runtime.",
        )
    except subprocess.TimeoutExpired:
        return (Submission.STATUS_ERROR, "Lean execution timed out.")
    finally:
        if source_path is not None:
            try:
                source_path.unlink()
            except Exception:
                pass


@method_decorator([login_required, require_POST], name="dispatch")
class ProblemRunView(View):
    def post(self, request, pk):
        problem = get_object_or_404(Problem, pk=pk)
        code = request.POST.get("code")
        if code is None:
            return HttpResponseBadRequest("Missing code payload")

        source_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w+", suffix=".lean", delete=False
            ) as source_file:
                source_file.write(code)
                source_path = Path(source_file.name)

            command = [get_lean_executable(), str(source_path)]
            result = subprocess.run(
                command,
                cwd=source_path.parent,
                capture_output=True,
                text=True,
                timeout=12,
            )
            response = {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except FileNotFoundError:
            response = {
                "error": "Lean executable not found. Install Lean on the server or configure a Lean runtime.",
            }
        except subprocess.TimeoutExpired:
            response = {
                "error": "Lean execution timed out.",
            }
        finally:
            if source_path is not None:
                try:
                    source_path.unlink()
                except Exception:
                    pass

        return JsonResponse(response)


@method_decorator([login_required, require_POST], name="dispatch")
class ProblemSubmitView(View):
    def post(self, request, pk):
        problem = get_object_or_404(Problem, pk=pk)
        code = request.POST.get("code")
        if code is None:
            return HttpResponseBadRequest("Missing code payload")

        submission = Submission.objects.create(
            problem=problem,
            user=request.user,
            code=code,
            status=Submission.STATUS_PENDING,
        )

        status, result = grade_lean_submission(problem, code)
        submission.status = status
        submission.result = result
        submission.save(update_fields=["status", "result"])

        return JsonResponse(
            {
                "submission_id": submission.pk,
                "status": status,
                "result": result,
            }
        )

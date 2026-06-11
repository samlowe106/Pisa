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
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from ..forms import (
    AssignmentForm,
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

ProblemFormSet = inlineformset_factory(
    Assignment,
    Problem,
    form=ProblemForm,
    extra=1,
    can_delete=True,
    fields=[
        "title",
        "statement",
        "required_code",
        "grading_stub",
        "points",
        "order",
    ],
)

ProblemBlockFormSet = inlineformset_factory(
    Problem,
    ProblemBlock,
    form=ProblemBlockForm,
    extra=1,
    can_delete=True,
    fields=["block_type", "content", "order"],
)


class AssignmentListView(LoginRequiredMixin, ListView):
    model = Assignment
    template_name = "homework/assignment_list.html"
    context_object_name = "assignments"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Assignment.objects.filter(
                course__in=self.request.user.courses_taught.all()
            ).order_by("-created_at")
        return Assignment.objects.filter(
            is_published=True, course__students=self.request.user
        ).order_by("-created_at")


class AssignmentDetailView(LoginRequiredMixin, DetailView):
    model = Assignment
    template_name = "homework/assignment_detail.html"
    context_object_name = "assignment"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Assignment.objects.filter(
                course__in=self.request.user.courses_taught.all()
            )
        return Assignment.objects.filter(
            is_published=True, course__students=self.request.user
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_staff:
            context["source_files"] = self.object.source_files.all()
        else:
            context["source_files"] = self.object.source_files.filter(visible=True)
        return context


class AssignmentCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"
    success_url = reverse_lazy("homework:dashboard")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["course"].queryset = Course.objects.filter(
            instructor=self.request.user
        )
        form.fields["source_files"].queryset = LeanSourceFile.objects.filter(
            created_by=self.request.user
        )
        return form

    def get_initial(self):
        initial = super().get_initial()
        course_slug = self.kwargs.get("course_slug") or self.request.GET.get("course")
        if course_slug:
            course = get_object_or_404(
                Course, slug=course_slug, instructor=self.request.user
            )
            initial["course"] = course
        return initial

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
        return self.form_invalid(form)

    def test_func(self):
        return self.request.user.is_staff


class AssignmentUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"

    def get_queryset(self):
        return Assignment.objects.filter(
            course__in=self.request.user.courses_taught.all()
        )

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["course"].queryset = Course.objects.filter(
            instructor=self.request.user
        )
        form.fields["source_files"].queryset = LeanSourceFile.objects.filter(
            created_by=self.request.user
        )
        return form

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
        self.assignment = get_object_or_404(
            Assignment,
            slug=kwargs["assignment_slug"],
            course__in=request.user.courses_taught.all(),
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.assignment
        if self.request.POST:
            context["block_formset"] = ProblemBlockFormSet(self.request.POST)
        else:
            context["block_formset"] = ProblemBlockFormSet()
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        block_formset = context["block_formset"]
        if block_formset.is_valid():
            form.instance.assignment = self.assignment
            self.object = form.save()
            block_formset.instance = self.object
            block_formset.save()
            return super().form_valid(form)
        return self.form_invalid(form)

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

    def get_queryset(self):
        return Problem.objects.filter(
            assignment__course__in=self.request.user.courses_taught.all()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.object.assignment
        if self.request.POST:
            context["block_formset"] = ProblemBlockFormSet(
                self.request.POST, instance=self.object
            )
        else:
            context["block_formset"] = ProblemBlockFormSet(instance=self.object)
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        block_formset = context["block_formset"]
        if block_formset.is_valid():
            self.object = form.save()
            block_formset.instance = self.object
            block_formset.save()
            return super().form_valid(form)
        return self.form_invalid(form)

    def get_success_url(self):
        return reverse_lazy("homework:problem_detail", kwargs={"pk": self.object.pk})

    def test_func(self):
        return self.request.user.is_staff


class ProblemDetailView(LoginRequiredMixin, DetailView):
    model = Problem
    template_name = "homework/problem_detail.html"
    context_object_name = "problem"

    def get_queryset(self):
        queryset = Problem.objects.select_related("assignment", "assignment__course")
        if self.request.user.is_staff:
            return queryset.filter(
                assignment__course__in=self.request.user.courses_taught.all()
            )
        return queryset.filter(
            assignment__course__students=self.request.user,
            assignment__is_published=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["submissions"] = self.object.submissions.filter(user=self.request.user)
        if self.request.user.is_staff:
            context["source_files"] = self.object.assignment.source_files.all()
        else:
            context["source_files"] = self.object.assignment.source_files.filter(
                visible=True
            )
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


def sanitize_lean_output(output: str, keep_internal: bool = False) -> str:
    if keep_internal or not output:
        return output or ""

    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^info:", stripped, re.IGNORECASE):
            continue
        lines.append(line)

    return "\n".join(lines)


def filter_lean_response(response: dict, keep_internal: bool) -> dict:
    filtered = response.copy()
    for key in ("stdout", "stderr"):
        if key in filtered and filtered[key] is not None:
            filtered[key] = sanitize_lean_output(filtered[key], keep_internal)
    return filtered


def grade_lean_submission(
    problem: Problem, code: str, keep_internal: bool = False
) -> tuple[str, str]:
    if problem.required_code and problem.required_code.strip() not in code:
        return (
            Submission.STATUS_FAILED,
            "Your submission does not include the required code snippet.",
        )

    # Include assignment source files first, then combine problem blocks in order
    full_code = ""
    for source_file in problem.assignment.source_files.order_by("title"):
        if source_file.content:
            full_code += source_file.content + "\n\n"

    for block in problem.blocks.order_by("order"):
        if block.block_type in ["fixed_code", "editable_code"]:
            if block.content:
                full_code += block.content + "\n\n"

    # If no blocks were included, use the submitted code directly
    if not full_code.strip():
        full_code = code

    source_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w+", suffix=".lean", delete=False
        ) as source_file:
            source_file.write(full_code)
            if problem.grading_stub:
                source_file.write("\n\n")
                source_file.write(problem.grading_stub)
            source_path = Path(source_file.name)

        command = [get_lean_executable(), str(source_path)]
        try:
            result = subprocess.run(
                command,
                cwd=source_path.parent,
                capture_output=True,
                text=True,
                timeout=getattr(settings, "LEAN_TIMEOUT", 60),
            )
        except subprocess.TimeoutExpired as e:
            # include any partial output to help debugging
            out = sanitize_lean_output(
                getattr(e, "stdout", None) or getattr(e, "output", None) or "",
                keep_internal=keep_internal,
            )
            err = sanitize_lean_output(
                getattr(e, "stderr", None) or "",
                keep_internal=keep_internal,
            )
            combined = (err + "\n" + out).strip() or ""
            msg = "Lean execution timed out."
            if combined:
                msg += "\nPartial output:\n" + combined
            return (Submission.STATUS_ERROR, msg)

        if result.returncode == 0:
            return (
                Submission.STATUS_PASSED,
                sanitize_lean_output(
                    result.stdout or "Lean ran with no errors :)",
                    keep_internal=keep_internal,
                ),
            )
        return (
            Submission.STATUS_FAILED,
            sanitize_lean_output(
                result.stderr
                or result.stdout
                or f"Lean exited with code {result.returncode}",
                keep_internal=keep_internal,
            ),
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
        if request.user.is_staff:
            get_object_or_404(
                Problem,
                pk=pk,
                assignment__course__in=request.user.courses_taught.all(),
            )
        else:
            get_object_or_404(
                Problem,
                pk=pk,
                assignment__is_published=True,
                assignment__course__students=request.user,
            )
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
            try:
                result = subprocess.run(
                    command,
                    cwd=source_path.parent,
                    capture_output=True,
                    text=True,
                    timeout=getattr(settings, "LEAN_TIMEOUT", 60),
                )
                response = filter_lean_response(
                    {
                        "returncode": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    },
                    keep_internal=request.user.is_staff,
                )
            except subprocess.TimeoutExpired as e:
                out = getattr(e, "stdout", None) or getattr(e, "output", None) or ""
                err = getattr(e, "stderr", None) or ""
                response = filter_lean_response(
                    {
                        "error": "Lean execution timed out.",
                        "stdout": out,
                        "stderr": err,
                    },
                    keep_internal=request.user.is_staff,
                )
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
        if request.user.is_staff:
            problem = get_object_or_404(
                Problem,
                pk=pk,
                assignment__course__in=request.user.courses_taught.all(),
            )
        else:
            problem = get_object_or_404(
                Problem,
                pk=pk,
                assignment__is_published=True,
                assignment__course__students=request.user,
            )
        code = request.POST.get("code")
        if code is None:
            return HttpResponseBadRequest("Missing code payload")

        submission = Submission.objects.create(
            problem=problem,
            user=request.user,
            code=code,
            status=Submission.STATUS_PENDING,
        )

        status, result = grade_lean_submission(
            problem, code, keep_internal=request.user.is_staff
        )
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

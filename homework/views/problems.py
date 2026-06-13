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
    UpdateView,
    View,
)

from ..forms import (
    ProblemBlockForm,
    ProblemForm,
)
from ..models import (
    Assignment,
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
            context["source_files"] = self.object.assignment.source_files.order_by("pk")
        else:
            context["source_files"] = self.object.assignment.source_files.filter(
                visible=True
            ).order_by("pk")
        context["editable_blocks"] = self.object.blocks.filter(
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE
        ).order_by("order")
        return context


def get_lean_executable() -> str:
    explicit = getattr(settings, "LEAN_EXECUTABLE", None)
    if explicit:
        # print(f"Lean executable: {explicit}")
        return explicit

    found = shutil.which("lean")
    if found:
        # print(f"Lean executable: {found}")
        return found

    elan_path = Path.home() / ".elan" / "bin" / "lean"
    if elan_path.exists():
        # print(f"Lean executable: {elan_path}")
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


def parse_lean_feedback(
    stdout: str | None, stderr: str | None, returncode: int | None = None
) -> dict:
    goals: list[str] = []
    messages: list[str] = []
    errors: list[str] = []

    for stream in (stderr or "", stdout or ""):
        for line in stream.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            lower = stripped.lower()
            if lower.startswith("goal:"):
                goals.append(stripped[len("goal:") :].strip())
            elif lower.startswith("msg:") or lower.startswith("message:"):
                messages.append(stripped.split(":", 1)[1].strip())
            elif re.search(r"\berror\b", lower):
                errors.append(line)
            elif re.search(r"\bwarning\b", lower):
                messages.append(line)
            elif "⊢" in line:
                goals.append(line)
            else:
                messages.append(line)

    if returncode == 0 and not goals and not messages and not errors:
        if stdout and stdout.strip():
            messages.append(stdout.strip())
        else:
            messages.append("Lean ran with no errors :)")

    return {
        "goals": goals,
        "messages": messages,
        "errors": errors,
    }


def build_lean_run_response(response: dict, keep_internal: bool) -> dict:
    filtered = filter_lean_response(response, keep_internal)
    parsed = parse_lean_feedback(
        filtered.get("stdout"), filtered.get("stderr"), filtered.get("returncode")
    )
    filtered.update(parsed)
    return filtered


def assemble_lean_submission_source(
    problem: Problem, post_data
) -> tuple[str, str | None]:
    editable_blocks = list(
        problem.blocks.filter(
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE
        ).order_by("order")
    )
    submitted_editables = {}

    for block in editable_blocks:
        field_name = f"editable_code_{block.pk}"
        block_value = post_data.get(field_name)
        if block_value is None and len(editable_blocks) == 1:
            block_value = post_data.get("code")
        if block_value is None:
            return "", f"Missing submission for editable block {block.pk}."
        submitted_editables[block.pk] = block_value

    if editable_blocks:
        full_code = ""
        for source_file in problem.assignment.source_files.order_by("pk"):
            if source_file.content:
                full_code += source_file.content + "\n\n"

        for block in problem.blocks.order_by("order"):
            if block.block_type == ProblemBlock.BLOCK_TYPE_FIXED_CODE:
                if block.content:
                    full_code += block.content + "\n\n"
            elif block.block_type == ProblemBlock.BLOCK_TYPE_EDITABLE_CODE:
                full_code += submitted_editables.get(block.pk, "") + "\n\n"

        return full_code, None

    return post_data.get("code", ""), None


def grade_lean_submission(
    problem: Problem, code: str, keep_internal: bool = False
) -> tuple[str, str]:
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
        submission_code, error = assemble_lean_submission_source(problem, request.POST)
        if error is not None:
            return HttpResponseBadRequest(error)

        source_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w+", suffix=".lean", delete=False
            ) as source_file:
                source_file.write(submission_code)
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
                response = build_lean_run_response(
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
                response = build_lean_run_response(
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
                "goals": [],
                "messages": [],
                "errors": ["Lean executable not found."],
            }
        except subprocess.TimeoutExpired:
            response = {
                "error": "Lean execution timed out.",
                "goals": [],
                "messages": [],
                "errors": ["Lean execution timed out."],
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
        submission_code, error = assemble_lean_submission_source(problem, request.POST)
        if error is not None:
            return HttpResponseBadRequest(error)

        submission = Submission.objects.create(
            problem=problem,
            user=request.user,
            code=submission_code,
            status=Submission.STATUS_PENDING,
        )

        status, result = grade_lean_submission(
            problem, submission_code, keep_internal=request.user.is_staff
        )
        submission.status = status
        submission.result = result
        submission.save(update_fields=["status", "result"])

        score = problem.points if status == Submission.STATUS_PASSED else 0
        return JsonResponse(
            {
                "submission_id": submission.pk,
                "status": status,
                "result": result,
                "score": score,
                "possible_points": problem.points,
            }
        )

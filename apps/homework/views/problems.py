import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import lean_policy, sandbox

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.forms import inlineformset_factory
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
    accessible_problems,
    editable_courses,
    editable_problems,
)
from .mixins import FormsetMixin

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


def _problem_by_number(problem_queryset, kwargs):
    """Resolve a problem from nested URL kwargs (course_slug, assignment_slug, number).

    The URL number is the problem's 1-based position within its assignment; the queryset
    carries the caller's access filter, so an inaccessible problem 404s.
    """
    assignment = get_object_or_404(
        Assignment,
        course__slug=kwargs["course_slug"],
        slug=kwargs["assignment_slug"],
    )
    problems = list(problem_queryset.filter(assignment=assignment))
    number = kwargs["number"]
    if not 1 <= number <= len(problems):
        raise Http404("No such problem.")
    return problems[number - 1]


def build_problem_pager(number: int, total: int) -> dict | None:
    """Bottom-of-page nav for stepping between problems in an assignment.

    Shows a 3-wide window of problem numbers around the current one, snapped to the ends so
    the actual first/last numbers appear (rather than an ellipsis) when the window is already
    against that edge. The "First"/"Last" jump links — each paired with an ellipsis — appear
    only when the window does not already reach that edge, and an edge that is only one step
    away is absorbed into the window so we never render an ellipsis that hides nothing.
    """
    if total <= 1:
        return None

    if number <= 3:
        lo, hi = 1, min(3, total)
    elif number >= total - 2:
        lo, hi = max(1, total - 2), total
    else:
        lo, hi = number - 1, number + 1

    # Absorb an edge that's only one step beyond the window (hiding a single number behind
    # "First …"/"… Last" would be pointless), e.g. number 3 of 10 shows 1 2 3, not 2 3 4.
    if lo == 2:
        lo = 1
    if hi == total - 1:
        hi = total

    return {
        "prev": number - 1 if number > 1 else None,
        "next": number + 1 if number < total else None,
        "show_first": lo > 1,
        "numbers": list(range(lo, hi + 1)),
        "show_last": hi < total,
        "current": number,
        "total": total,
    }


class ProblemCreateView(LoginRequiredMixin, FormsetMixin, CreateView):
    model = Problem
    form_class = ProblemForm
    template_name = "homework/problem_form.html"
    formset_class = ProblemBlockFormSet
    formset_context_name = "block_formset"

    def dispatch(self, request, *args, **kwargs):
        self.assignment = get_object_or_404(
            Assignment,
            course__slug=kwargs["course_slug"],
            slug=kwargs["assignment_slug"],
            course__in=editable_courses(request.user),
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["visible_source_files"].queryset = (
            self.assignment.source_files.all()
        )
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.assignment
        return context

    def form_valid(self, form):
        form.instance.assignment = self.assignment
        return super().form_valid(form)

    def get_success_url(self):
        return self.object.get_absolute_url()


class ProblemUpdateView(LoginRequiredMixin, FormsetMixin, UpdateView):
    model = Problem
    form_class = ProblemForm
    template_name = "homework/problem_form.html"
    formset_class = ProblemBlockFormSet
    formset_context_name = "block_formset"

    def get_queryset(self):
        return editable_problems(self.request.user)

    def get_object(self, queryset=None):
        return _problem_by_number(
            queryset if queryset is not None else self.get_queryset(), self.kwargs
        )

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["visible_source_files"].queryset = (
            self.object.assignment.source_files.all()
        )
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["assignment"] = self.object.assignment
        return context

    def get_success_url(self):
        return self.object.get_absolute_url()


class ProblemDetailView(LoginRequiredMixin, DetailView):
    model = Problem
    template_name = "homework/problem_detail.html"
    context_object_name = "problem"

    def get_queryset(self):
        return accessible_problems(self.request.user).select_related(
            "assignment", "assignment__course"
        )

    def get_object(self, queryset=None):
        return _problem_by_number(
            queryset if queryset is not None else self.get_queryset(), self.kwargs
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["number"] = self.kwargs["number"]
        context["can_edit"] = self.object.assignment.course.is_instructor(
            self.request.user
        )
        context["pager"] = build_problem_pager(
            self.kwargs["number"], self.object.assignment.problems.count()
        )
        context["submissions"] = self.object.submissions.filter(
            user=self.request.user
        ).select_related("problem__assignment")
        due = self.object.assignment.due_date
        context["due_date"] = due
        context["past_due"] = bool(due and due < timezone.now())
        if self.object.assignment.course.is_course_staff(self.request.user):
            # Course staff (incl. TAs) see every imported file, tagged with whether
            # students can view it here.
            visible_ids = set(
                self.object.visible_source_files.values_list("id", flat=True)
            )
            files = list(self.object.assignment.source_files.order_by("pk"))
            for source_file in files:
                source_file.show_visibility = True
                source_file.visible_here = source_file.id in visible_ids
            context["source_files"] = files
        else:
            context["source_files"] = self.object.visible_source_files.order_by("pk")
        context["editable_blocks"] = self.object.blocks.filter(
            block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE
        ).order_by("order")
        # Load CodeMirror (base.html) for the editor + highlighted source-file viewers.
        context["use_codemirror"] = True
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
) -> tuple[str, str, str | None]:
    """Build the full compilable document and, separately, just the *student's* editable text
    (so policy checks scan student code, not the instructor's prefix). Returns
    ``(full_code, student_code, error)``."""
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
            return "", "", f"Missing submission for editable block {block.pk}."
        submitted_editables[block.pk] = block_value

    if editable_blocks:
        full_code = ""
        for source_file in problem.assignment.source_files.order_by("pk"):
            if source_file.content:
                full_code += source_file.content + "\n\n"

        student_code = ""
        for block in problem.blocks.order_by("order"):
            if block.block_type == ProblemBlock.BLOCK_TYPE_FIXED_CODE:
                if block.content:
                    full_code += block.content + "\n\n"
            elif block.block_type == ProblemBlock.BLOCK_TYPE_EDITABLE_CODE:
                editable = submitted_editables.get(block.pk, "")
                full_code += editable + "\n\n"
                student_code += editable + "\n\n"

        return full_code, student_code, None

    code = post_data.get("code", "")
    return code, code, None


def run_lean_process(code: str, extra: str = "") -> dict:
    """Write ``code`` (plus optional ``extra``, e.g. a grading stub) to a temp ``.lean`` file
    and run Lean on it, always cleaning the file up. Returns one of:

    - ``{"returncode": int, "stdout": str, "stderr": str}`` — Lean ran
    - ``{"timeout": True, "stdout": str, "stderr": str}`` — timed out (with partial output)
    - ``{"missing": True}`` — the Lean executable could not be found
    """
    workdir = None
    try:
        # Own temp dir per run, so the sandbox can bind *just* this directory writable.
        workdir = tempfile.mkdtemp(prefix="pisa_lean_")
        source_path = Path(workdir) / "submission.lean"
        source_path.write_text(code + (f"\n\n{extra}" if extra else ""))

        try:
            argv = sandbox.wrap_argv(
                [get_lean_executable(), str(source_path)], workdir=workdir
            )
        except FileNotFoundError:
            return {"missing": True}

        wall_timeout = getattr(settings, "LEAN_TIMEOUT", 60)
        # CPU-time backstop: a process can't outrun the wall clock, but a multi-threaded one
        # could burn more CPU, so cap it at a generous multiple of the wall timeout.
        cpu_seconds = (
            getattr(settings, "LEAN_SANDBOX_CPU_SECONDS", 0) or wall_timeout * 4
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **sandbox.popen_kwargs(cpu_seconds=cpu_seconds),
            )
        except FileNotFoundError:
            return {"missing": True}

        try:
            stdout, stderr = process.communicate(timeout=wall_timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole process group so no Lean child outlives the request.
            sandbox.kill_process_group(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            return {"timeout": True, "stdout": stdout or "", "stderr": stderr or ""}
        return {
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def grade_lean_submission(
    problem: Problem,
    code: str,
    student_code: str | None = None,
    keep_internal: bool = False,
) -> tuple[str, str]:
    if problem.required_code and problem.required_code.strip() not in code:
        return (
            Submission.STATUS_FAILED,
            "Your submission does not include the required code snippet.",
        )

    # Layer 1: reject disallowed constructs in the *student's* code before running Lean.
    allowed = frozenset(problem.allowed_constructs or ())
    hits = lean_policy.scan(
        student_code if student_code is not None else code, allowed=allowed
    )
    if hits:
        listed = "\n".join(f"  • {rule.id}: {rule.reason}" for rule in hits)
        return (
            Submission.STATUS_FAILED,
            "Your submission uses constructs that aren't allowed here:\n" + listed,
        )

    # Append a `#print axioms` audit of the target declaration so the soundness check can't be
    # evaded (catches sorry/admit/axiom/native_decide however they got in).
    extra = problem.grading_stub or ""
    if problem.axiom_target:
        extra = (
            extra + "\n\n" if extra else ""
        ) + f"#print axioms {problem.axiom_target}"

    result = run_lean_process(code, extra)

    if result.get("missing"):
        return (
            Submission.STATUS_ERROR,
            "Lean executable not found. Install Lean on the server or configure a Lean runtime.",
        )
    if result.get("timeout"):
        out = sanitize_lean_output(result["stdout"], keep_internal=keep_internal)
        err = sanitize_lean_output(result["stderr"], keep_internal=keep_internal)
        combined = (err + "\n" + out).strip() or ""
        msg = "Lean execution timed out."
        if combined:
            msg += "\nPartial output:\n" + combined
        return (Submission.STATUS_ERROR, msg)

    if result["returncode"] == 0:
        # Layer 2: the proof compiled — now verify it doesn't lean on forbidden axioms.
        if problem.axiom_target:
            allowed_axioms = frozenset(
                name.strip()
                for name in problem.allowed_axioms.split(",")
                if name.strip()
            )
            bad = lean_policy.forbidden_axioms(result["stdout"], allowed=allowed_axioms)
            if bad is None:
                return (
                    Submission.STATUS_FAILED,
                    f"Could not verify the axioms of '{problem.axiom_target}'. "
                    "Check that this declaration exists in the submission.",
                )
            if bad:
                if "sorryAx" in bad:
                    return (
                        Submission.STATUS_FAILED,
                        "Your proof is incomplete — it relies on `sorry`.",
                    )
                return (
                    Submission.STATUS_FAILED,
                    "Your proof depends on disallowed axioms: "
                    + ", ".join(sorted(bad)),
                )
        return (
            Submission.STATUS_PASSED,
            sanitize_lean_output(
                result["stdout"] or "Lean ran with no errors :)",
                keep_internal=keep_internal,
            ),
        )
    return (
        Submission.STATUS_FAILED,
        sanitize_lean_output(
            result["stderr"]
            or result["stdout"]
            or f"Lean exited with code {result['returncode']}",
            keep_internal=keep_internal,
        ),
    )


@method_decorator([login_required, require_POST], name="dispatch")
class ProblemRunView(View):
    def post(self, request, pk):
        problem = get_object_or_404(accessible_problems(request.user), pk=pk)
        submission_code, _student_code, error = assemble_lean_submission_source(
            problem, request.POST
        )
        if error is not None:
            return HttpResponseBadRequest(error)

        result = run_lean_process(submission_code)
        if result.get("missing"):
            response = {
                "error": "Lean executable not found. Install Lean on the server or configure a Lean runtime.",
                "goals": [],
                "messages": [],
                "errors": ["Lean executable not found."],
            }
        elif result.get("timeout"):
            response = build_lean_run_response(
                {
                    "error": "Lean execution timed out.",
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                keep_internal=request.user.is_staff,
            )
        else:
            response = build_lean_run_response(
                {
                    "returncode": result["returncode"],
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                keep_internal=request.user.is_staff,
            )
        return JsonResponse(response)


@method_decorator([login_required, require_POST], name="dispatch")
class ProblemSubmitView(View):
    def post(self, request, pk):
        problem = get_object_or_404(accessible_problems(request.user), pk=pk)
        submission_code, student_code, error = assemble_lean_submission_source(
            problem, request.POST
        )
        if error is not None:
            return HttpResponseBadRequest(error)

        submission = Submission.objects.create(
            problem=problem,
            user=request.user,
            code=submission_code,
            status=Submission.STATUS_PENDING,
        )

        status, result = grade_lean_submission(
            problem, submission_code, student_code, keep_internal=request.user.is_staff
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


@method_decorator([login_required, require_POST], name="dispatch")
class ProblemReorderView(View):
    """Persist a drag-and-drop reordering of an assignment's problems (staff only).

    Accepts JSON ``{"order": [problem_pk, ...]}`` listing every problem in the assignment
    in its new order, and writes each problem's 0-based index back to ``Problem.order``
    (which drives ``Problem.position`` and the nested URLs). Scoping the assignment to the
    requester's taught courses keeps non-instructors out.
    """

    def post(self, request, course_slug, assignment_slug):
        assignment = get_object_or_404(
            Assignment,
            course__slug=course_slug,
            slug=assignment_slug,
            course__in=editable_courses(request.user),
        )
        try:
            payload = json.loads(request.body)
            ordered_ids = [int(pk) for pk in payload["order"]]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            return HttpResponseBadRequest("Invalid reorder payload.")

        problems = list(assignment.problems.all())
        if sorted(ordered_ids) != sorted(problem.pk for problem in problems):
            return HttpResponseBadRequest(
                "Reorder must list every problem in the assignment exactly once."
            )

        index_by_pk = {pk: index for index, pk in enumerate(ordered_ids)}
        for problem in problems:
            problem.order = index_by_pk[problem.pk]
        Problem.objects.bulk_update(problems, ["order"])
        return JsonResponse({"ok": True})

import json

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
from ..lean_runner import (
    assemble_lean_submission_source,
    build_lean_run_response,
    grade_lean_submission,
    run_lean_process,
)
from ..models import (
    Assignment,
    Problem,
    ProblemBlock,
    Submission,
)
from ..selectors import accessible_problems, editable_courses, editable_problems
from .mixins import FormsetMixin, ResolvedObjectMixin

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


class ProblemUpdateView(
    LoginRequiredMixin, FormsetMixin, ResolvedObjectMixin, UpdateView
):
    model = Problem
    form_class = ProblemForm
    template_name = "homework/problem_form.html"
    formset_class = ProblemBlockFormSet
    formset_context_name = "block_formset"
    object_resolver = staticmethod(_problem_by_number)

    def get_queryset(self):
        return editable_problems(self.request.user)

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


class ProblemDetailView(LoginRequiredMixin, ResolvedObjectMixin, DetailView):
    model = Problem
    template_name = "homework/problem_detail.html"
    context_object_name = "problem"
    object_resolver = staticmethod(_problem_by_number)

    def get_queryset(self):
        return accessible_problems(self.request.user).select_related(
            "assignment", "assignment__course"
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
        elif result.get("sandbox_error"):
            response = build_lean_run_response(
                {
                    "error": "The server's Lean sandbox failed to start, so Lean could not "
                    "run. This is a server problem — please tell your instructor.",
                    "stdout": result["stdout"],
                    "stderr": result["stderr"],
                },
                keep_internal=request.user.is_staff,
            )
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

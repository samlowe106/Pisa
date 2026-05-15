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

from .forms import AssignmentForm, CourseForm, ProblemBlockForm, ProblemForm
from .models import Assignment, Course, Problem, ProblemBlock, Submission

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


class CourseListView(LoginRequiredMixin, ListView):
    model = Course
    template_name = "homework/course_list.html"
    context_object_name = "courses"

    def get_queryset(self):
        if self.request.user.is_staff:
            return Course.objects.filter(instructor=self.request.user).order_by(
                "-created_at"
            )
        return Course.objects.all().order_by("-created_at")


class CourseDetailView(LoginRequiredMixin, DetailView):
    model = Course
    template_name = "homework/course_detail.html"
    context_object_name = "course"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_queryset(self):
        return Course.objects.all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = self.object
        if self.request.user.is_staff:
            context["assignments"] = course.assignments.order_by("-created_at")
        else:
            context["assignments"] = course.assignments.filter(
                is_published=True
            ).order_by("-created_at")
        return context


class CourseCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Course
    form_class = CourseForm
    template_name = "homework/course_form.html"
    success_url = reverse_lazy("homework:course_list")

    def form_valid(self, form):
        form.instance.instructor = self.request.user
        return super().form_valid(form)

    def test_func(self):
        return self.request.user.is_staff


class CourseUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Course
    form_class = CourseForm
    template_name = "homework/course_form.html"

    def get_queryset(self):
        return Course.objects.filter(instructor=self.request.user)

    def test_func(self):
        return self.request.user.is_staff


class CourseEnrollView(LoginRequiredMixin, View):
    def post(self, request, slug):
        course = get_object_or_404(Course, slug=slug)
        if request.user.is_staff:
            return HttpResponseBadRequest("Instructors cannot enroll as students.")
        course.students.add(request.user)
        return redirect("homework:course_detail", slug=slug)


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

    # Combine all code blocks in order
    full_code = ""
    for block in problem.blocks.order_by("order"):
        if block.block_type in ["fixed_code", "editable_code"]:
            if block.content:
                full_code += block.content + "\n\n"

    # If no blocks, use the submitted code directly
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

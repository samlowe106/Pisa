from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import AssignmentForm
from ..models import Assignment, LeanSourceFile
from ..selectors import (
    accessible_assignments,
    editable_assignments,
    editable_courses,
    is_student_anywhere,
)
from .mixins import FormsetMixin, ResolvedObjectMixin
from .problems import ProblemFormSet


def _assignment_by_slug(assignment_queryset, kwargs):
    """Resolve an assignment from nested URL kwargs (course_slug, assignment_slug)."""
    return get_object_or_404(
        assignment_queryset,
        course__slug=kwargs["course_slug"],
        slug=kwargs["assignment_slug"],
    )


class AssignmentListView(LoginRequiredMixin, ListView):
    """A student's published assignments across the courses they're enrolled in. Assignments
    are course-specific, so this page is only for students — staff manage assignments from
    each course's page."""

    model = Assignment
    template_name = "homework/assignment_list.html"
    context_object_name = "assignments"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_student_anywhere(request.user):
            return redirect("homework:course_list")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Assignment.objects.filter(
                is_published=True, course__students=self.request.user
            )
            .select_related("course")
            .order_by("-created_at")
        )


class AssignmentDetailView(LoginRequiredMixin, ResolvedObjectMixin, DetailView):
    model = Assignment
    template_name = "homework/assignment_detail.html"
    context_object_name = "assignment"
    object_resolver = staticmethod(_assignment_by_slug)

    def get_queryset(self):
        return accessible_assignments(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        course = self.object.course
        user = self.request.user
        context["can_edit"] = course.is_instructor(user)
        if course.is_course_staff(user):
            # Course staff (incl. TAs) see every imported file.
            context["source_files"] = self.object.source_files.order_by("pk")
        else:
            # Students see files marked visible on at least one of the assignment's problems.
            context["source_files"] = (
                LeanSourceFile.objects.filter(
                    visible_in_problems__assignment=self.object
                )
                .distinct()
                .order_by("pk")
            )
        # Load CodeMirror so imported source files are syntax-highlighted (base.html).
        context["use_codemirror"] = True
        return context


class AssignmentCreateView(LoginRequiredMixin, FormsetMixin, CreateView):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"
    formset_class = ProblemFormSet
    formset_context_name = "problem_formset"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["course"].queryset = editable_courses(self.request.user)
        form.fields["source_files"].queryset = LeanSourceFile.objects.filter(
            created_by=self.request.user
        )
        return form

    def get_initial(self):
        initial = super().get_initial()
        course_slug = self.kwargs.get("course_slug") or self.request.GET.get("course")
        if course_slug:
            initial["course"] = get_object_or_404(
                editable_courses(self.request.user), slug=course_slug
            )
        return initial

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return self.object.get_absolute_url()


class AssignmentUpdateView(
    LoginRequiredMixin, FormsetMixin, ResolvedObjectMixin, UpdateView
):
    model = Assignment
    form_class = AssignmentForm
    template_name = "homework/assignment_form.html"
    formset_class = ProblemFormSet
    formset_context_name = "problem_formset"
    object_resolver = staticmethod(_assignment_by_slug)

    def get_queryset(self):
        return editable_assignments(self.request.user)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["course"].queryset = editable_courses(self.request.user)
        form.fields["source_files"].queryset = LeanSourceFile.objects.filter(
            created_by=self.request.user
        )
        return form

    def get_success_url(self):
        return self.object.get_absolute_url()

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
)

from ..forms import AssignmentForm
from ..models import (
    Assignment,
    Course,
    LeanSourceFile,
)
from .problems import ProblemFormSet


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
            context["source_files"] = self.object.source_files.order_by("pk")
        else:
            context["source_files"] = self.object.source_files.filter(
                visible=True
            ).order_by("pk")
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

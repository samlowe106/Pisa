from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from ..forms import (
    CourseForm,
)
from ..models import (
    Course,
)


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

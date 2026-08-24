from typing import TYPE_CHECKING

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    ListView,
    UpdateView,
)

from ..forms import LeanSourceFileForm
from ..models import LeanSourceFile
from .mixins import InstructorAnywhereMixin

if TYPE_CHECKING:
    from django.http import HttpResponse


class LeanSourceFileListView(LoginRequiredMixin, InstructorAnywhereMixin, ListView):
    """An instructor's own reusable Lean source-file library."""

    model = LeanSourceFile
    template_name = "homework/lean_source_file_list.html"
    context_object_name = "source_files"

    # Not return-typed: self.request.user is `User | AnonymousUser` per django-stubs, and
    # LoginRequiredMixin's authentication guarantee isn't visible to the type checker, so a
    # `.filter(created_by=self.request.user)` here would need a narrowing cast, more
    # ceremony than the annotation is worth for this one line. Same below.
    def get_queryset(self):
        return LeanSourceFile.objects.filter(created_by=self.request.user)


class LeanSourceFileCreateView(LoginRequiredMixin, InstructorAnywhereMixin, CreateView):
    """Upload a new Lean source file, owned by the uploading instructor."""

    model = LeanSourceFile
    form_class = LeanSourceFileForm
    template_name = "homework/lean_source_file_form.html"
    success_url = reverse_lazy("homework:lean_source_file_list")

    def form_valid(self, form) -> HttpResponse:
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class LeanSourceFileUpdateView(LoginRequiredMixin, InstructorAnywhereMixin, UpdateView):
    """Edit a Lean source file the current instructor owns."""

    model = LeanSourceFile
    form_class = LeanSourceFileForm
    template_name = "homework/lean_source_file_form.html"
    success_url = reverse_lazy("homework:lean_source_file_list")

    def get_queryset(self):
        return LeanSourceFile.objects.filter(created_by=self.request.user)

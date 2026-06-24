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


class LeanSourceFileListView(LoginRequiredMixin, InstructorAnywhereMixin, ListView):
    model = LeanSourceFile
    template_name = "homework/lean_source_file_list.html"
    context_object_name = "source_files"

    def get_queryset(self):
        return LeanSourceFile.objects.filter(created_by=self.request.user)


class LeanSourceFileCreateView(LoginRequiredMixin, InstructorAnywhereMixin, CreateView):
    model = LeanSourceFile
    form_class = LeanSourceFileForm
    template_name = "homework/lean_source_file_form.html"
    success_url = reverse_lazy("homework:lean_source_file_list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class LeanSourceFileUpdateView(LoginRequiredMixin, InstructorAnywhereMixin, UpdateView):
    model = LeanSourceFile
    form_class = LeanSourceFileForm
    template_name = "homework/lean_source_file_form.html"
    success_url = reverse_lazy("homework:lean_source_file_list")

    def get_queryset(self):
        return LeanSourceFile.objects.filter(created_by=self.request.user)

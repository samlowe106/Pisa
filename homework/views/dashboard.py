from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from ..models import Assignment


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

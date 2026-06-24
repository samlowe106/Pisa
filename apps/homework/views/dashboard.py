from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import RedirectView


class DashboardView(LoginRequiredMixin, RedirectView):
    """The dashboard has been folded into the course list. Keep this URL working — it's the
    LOGIN_REDIRECT target and a bookmark — by redirecting to the course list."""

    pattern_name = "homework:course_list"

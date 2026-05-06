from django.urls import path

from . import views

app_name = "homework"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("assignments/", views.AssignmentListView.as_view(), name="assignment_list"),
    path(
        "assignments/create/",
        views.AssignmentCreateView.as_view(),
        name="assignment_create",
    ),
    path(
        "assignments/<slug:slug>/edit/",
        views.AssignmentUpdateView.as_view(),
        name="assignment_update",
    ),
    path(
        "assignments/<slug:slug>/",
        views.AssignmentDetailView.as_view(),
        name="assignment_detail",
    ),
    path(
        "assignments/<slug:assignment_slug>/problems/create/",
        views.ProblemCreateView.as_view(),
        name="problem_create",
    ),
    path(
        "problems/<int:pk>/", views.ProblemDetailView.as_view(), name="problem_detail"
    ),
    path(
        "problems/<int:pk>/edit/",
        views.ProblemUpdateView.as_view(),
        name="problem_update",
    ),
    path("problems/<int:pk>/run/", views.ProblemRunView.as_view(), name="problem_run"),
    path(
        "problems/<int:pk>/submit/",
        views.ProblemSubmitView.as_view(),
        name="problem_submit",
    ),
]

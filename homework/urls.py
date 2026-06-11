from django.urls import path

from . import views

app_name = "homework"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("grades/", views.GradesView.as_view(), name="grades"),
    path("courses/", views.CourseListView.as_view(), name="course_list"),
    path("courses/create/", views.CourseCreateView.as_view(), name="course_create"),
    path(
        "courses/<slug:slug>/edit/",
        views.CourseUpdateView.as_view(),
        name="course_update",
    ),
    path(
        "courses/<slug:slug>/enroll/",
        views.CourseEnrollView.as_view(),
        name="course_enroll",
    ),
    path(
        "courses/<slug:slug>/",
        views.CourseDetailView.as_view(),
        name="course_detail",
    ),
    path("assignments/", views.AssignmentListView.as_view(), name="assignment_list"),
    path(
        "assignments/create/",
        views.AssignmentCreateView.as_view(),
        name="assignment_create",
    ),
    path(
        "courses/<slug:course_slug>/assignments/create/",
        views.AssignmentCreateView.as_view(),
        name="assignment_create_for_course",
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
    path(
        "lean-files/",
        views.LeanSourceFileListView.as_view(),
        name="lean_source_file_list",
    ),
    path(
        "lean-files/create/",
        views.LeanSourceFileCreateView.as_view(),
        name="lean_source_file_create",
    ),
    path(
        "lean-files/<int:pk>/edit/",
        views.LeanSourceFileUpdateView.as_view(),
        name="lean_source_file_update",
    ),
    path(
        "courses/<slug:course_slug>/export/csv/",
        views.ExportGradesCSVView.as_view(),
        name="export_grades_csv",
    ),
    path(
        "courses/<slug:course_slug>/export/excel/",
        views.ExportGradesExcelView.as_view(),
        name="export_grades_excel",
    ),
]

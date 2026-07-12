from django.urls import path

from . import views

app_name = "homework"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("grades/", views.GradesView.as_view(), name="grades"),
    # region Courses
    path("courses/", views.CourseListView.as_view(), name="course_list"),
    path("courses/create/", views.CourseCreateView.as_view(), name="course_create"),
    # Specific course-scoped actions must precede the generic nested routes below so they
    # aren't swallowed by `courses/<course_slug>/<assignment_slug>/`.
    path(
        "courses/<slug:slug>/edit/",
        views.CourseUpdateView.as_view(),
        name="course_update",
    ),
    path(
        "courses/<slug:slug>/renew/",
        views.CourseRenewView.as_view(),
        name="course_renew",
    ),
    path(
        "courses/<slug:slug>/enroll/",
        views.CourseEnrollView.as_view(),
        name="course_enroll",
    ),
    path(
        "courses/<slug:slug>/members/add/",
        views.CourseAddMemberView.as_view(),
        name="course_add_member",
    ),
    path(
        "courses/<slug:slug>/members/remove/",
        views.CourseRemoveMemberView.as_view(),
        name="course_remove_member",
    ),
    path(
        "courses/<slug:course_slug>/assignments/create/",
        views.AssignmentCreateView.as_view(),
        name="assignment_create_for_course",
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
    path(
        "courses/<slug:slug>/", views.CourseDetailView.as_view(), name="course_detail"
    ),
    # endregion
    # region Assignments + problems, nested under their course
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/",
        views.AssignmentDetailView.as_view(),
        name="assignment_detail",
    ),
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/edit/",
        views.AssignmentUpdateView.as_view(),
        name="assignment_update",
    ),
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/problems/new/",
        views.ProblemCreateView.as_view(),
        name="problem_create",
    ),
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/problems/reorder/",
        views.ProblemReorderView.as_view(),
        name="problem_reorder",
    ),
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/<int:number>/",
        views.ProblemDetailView.as_view(),
        name="problem_detail",
    ),
    path(
        "courses/<slug:course_slug>/<slug:assignment_slug>/<int:number>/edit/",
        views.ProblemUpdateView.as_view(),
        name="problem_update",
    ),
    # endregion
    # region Student assignment index (assignments are created per-course)
    path("assignments/", views.AssignmentListView.as_view(), name="assignment_list"),
    # endregion
    # region Internal problem endpoints (kept keyed by id)
    path("problems/<int:pk>/run/", views.ProblemRunView.as_view(), name="problem_run"),
    path(
        "problems/<int:pk>/submit/",
        views.ProblemSubmitView.as_view(),
        name="problem_submit",
    ),
    # endregion
    # region Lean source files
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
    # endregion
]

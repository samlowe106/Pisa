from .assignments import (
    AssignmentCreateView,
    AssignmentDetailView,
    AssignmentListView,
    AssignmentUpdateView,
)
from .courses import (
    CourseAddMemberView,
    CourseCreateView,
    CourseDetailView,
    CourseEnrollView,
    CourseListView,
    CourseRemoveMemberView,
    CourseRenewView,
    CourseUpdateView,
)
from .dashboard import DashboardView
from .grades import ExportGradesCSVView, ExportGradesExcelView, GradesView
from .lean_source_files import (
    LeanSourceFileCreateView,
    LeanSourceFileListView,
    LeanSourceFileUpdateView,
)
from .problems import (
    ProblemCreateView,
    ProblemDetailView,
    ProblemReorderView,
    ProblemRunView,
    ProblemSubmitView,
    ProblemUpdateView,
)

__all__ = [
    "AssignmentCreateView",
    "AssignmentDetailView",
    "AssignmentListView",
    "AssignmentUpdateView",
    "CourseAddMemberView",
    "CourseCreateView",
    "CourseDetailView",
    "CourseEnrollView",
    "CourseListView",
    "CourseRemoveMemberView",
    "CourseRenewView",
    "CourseUpdateView",
    "DashboardView",
    "ExportGradesCSVView",
    "ExportGradesExcelView",
    "GradesView",
    "LeanSourceFileCreateView",
    "LeanSourceFileListView",
    "LeanSourceFileUpdateView",
    "ProblemCreateView",
    "ProblemDetailView",
    "ProblemReorderView",
    "ProblemRunView",
    "ProblemSubmitView",
    "ProblemUpdateView",
]

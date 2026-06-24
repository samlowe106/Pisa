"""Shared test helpers: skip decorators for env-dependent tests, and a role-matrix fixture."""

import shutil
import subprocess
import unittest

from django.contrib.auth import get_user_model

from apps.homework.models import Assignment, Course, Problem, ProblemBlock
from apps.homework.views.problems import get_lean_executable

User = get_user_model()


def _lean_available():
    try:
        get_lean_executable()
        return True
    except Exception:  # noqa: BLE001
        return False


def _bwrap_can_sandbox():
    """Whether bubblewrap can actually *create* a sandbox here — not just whether the binary is
    installed. Inside a container without the right capabilities bwrap is present but fails at
    ``mount`` ("Failed to make / slave"), so a binary-only check would let the Layer 2 tests run
    and fail. We probe with the same namespace/bind setup the real wrapper uses."""
    exe = shutil.which("bwrap")
    if not exe:
        return False
    try:
        probe = subprocess.run(
            [exe, "--unshare-all", "--ro-bind", "/", "/", "--proc", "/proc", "true"],
            capture_output=True,
            timeout=15,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Real-Lean tests run in CI's Docker image (Lean baked in) and skip elsewhere.
requires_lean = unittest.skipUnless(_lean_available(), "Lean executable not available")

# Layer 2 sandbox-isolation tests need a bubblewrap that can really build a sandbox here.
requires_bwrap = unittest.skipUnless(
    _bwrap_can_sandbox(), "bubblewrap cannot create a sandbox in this environment"
)


def make_role_matrix():
    """Create one user per role plus a published course/assignment/problem, and return them.

    Returns a dict: admin (is_staff), instructor, ta, student (enrolled), outsider (no relation),
    course, assignment, problem.
    """

    def user(username, **extra):
        u = User.objects.create_user(username=username, password="pw", **extra)
        return u

    admin = user("t_admin", is_staff=True)
    instructor = user("t_instructor")
    ta = user("t_ta")
    student = user("t_student")
    outsider = user("t_outsider")

    course = Course.objects.create(title="Test Course", slug="test-course")
    course.instructors.add(instructor)
    course.tas.add(ta)
    course.students.add(student)

    assignment = Assignment.objects.create(
        course=course,
        title="HW1",
        slug="hw1",
        created_by=instructor,
        is_published=True,
    )
    problem = Problem.objects.create(assignment=assignment, title="P1", points=1)
    ProblemBlock.objects.create(
        problem=problem,
        block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
        content="example : True := trivial\n",
        order=0,
    )
    return {
        "admin": admin,
        "instructor": instructor,
        "ta": ta,
        "student": student,
        "outsider": outsider,
        "course": course,
        "assignment": assignment,
        "problem": problem,
    }

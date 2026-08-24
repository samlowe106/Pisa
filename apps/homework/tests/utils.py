"""Shared test helpers: skip decorators for env-dependent tests, and a role-matrix fixture."""

import shutil
import subprocess
import unittest

from django.contrib.auth import get_user_model

from apps.homework.lean_runner import get_lean_executable
from apps.homework.models import Assignment, Course, Problem, ProblemBlock

User = get_user_model()


def _lean_available():
    try:
        get_lean_executable()
    except FileNotFoundError:
        return False
    else:
        return True


def _bwrap_can_sandbox():
    """Whether bubblewrap can actually *create* a sandbox here, not just whether the binary is
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
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    else:
        return probe.returncode == 0


# Real-Lean tests run in CI's Docker image (Lean baked in) and skip elsewhere.
requires_lean = unittest.skipUnless(_lean_available(), "Lean executable not available")

# Layer 2 sandbox-isolation tests need a bubblewrap that can really build a sandbox here.
requires_bwrap = unittest.skipUnless(
    _bwrap_can_sandbox(), "bubblewrap cannot create a sandbox in this environment"
)


def _seccomp_denies_ptrace():
    """Whether *this* process is already confined by a seccomp filter that denies ptrace, i.e.
    running inside a container with docker/seccomp/pisa.json applied via security_opt, not a
    bare host or dev container. ptrace(PTRACE_TRACEME) always succeeds on its own (it has no
    tracer to deny), so a plain permission check can't tell us this; only a seccomp ERRNO action
    makes the syscall itself fail, which is exactly what we're probing for."""
    probe_code = (
        "import ctypes, sys; "
        "libc = ctypes.CDLL(None, use_errno=True); "
        "rc = libc.ptrace(0, 0, 0, 0); "
        "sys.exit(0 if rc == -1 and ctypes.get_errno() == 1 else 1)"
    )
    try:
        probe = subprocess.run(
            ["python3", "-c", probe_code],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    else:
        return probe.returncode == 0


# The ptrace-denial test only means something inside a container carrying the hardened
# seccomp profile (docker/seccomp/pisa.json); elsewhere ptrace is expected to succeed.
requires_hardened_seccomp = unittest.skipUnless(
    _seccomp_denies_ptrace(),
    "not running under the hardened seccomp profile (docker/seccomp/pisa.json)",
)


def make_role_matrix():
    """Create one user per role plus a published course/assignment/problem, and return them.

    Returns a dict: admin (is_staff), instructor, ta, student (enrolled), outsider (no relation),
    course, assignment, problem.
    """

    def user(username, **extra):
        return User.objects.create_user(username=username, password="pw", **extra)

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

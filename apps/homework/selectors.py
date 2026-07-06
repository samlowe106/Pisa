"""Access-control query selectors: who may view or edit which courses/assignments/problems.

Every permission-scoped queryset the views use lives here, so the whole visibility surface is
auditable in one place. Admins (``user.is_staff``) see and edit everything; course staff
(instructors + TAs) see their courses' drafts; students see only published material.
"""

from django.db.models import Q

from .models import Assignment, Course, Problem


def _staff_course_ids(user):
    """Ids of courses where the user is instructor or TA (admins handled separately)."""
    return list(
        Course.objects.filter(Q(instructors=user) | Q(tas=user)).values_list(
            "id", flat=True
        )
    )


def is_student_anywhere(user):
    """True if the user is enrolled as a student in at least one course."""
    return Course.objects.filter(students=user).exists()


def accessible_assignments(user):
    """Assignments a user may view: everything in courses where they're course staff (admins
    see every course), plus published assignments in courses where they're a student."""
    if user.is_staff:
        return Assignment.objects.all()
    return Assignment.objects.filter(
        Q(course_id__in=_staff_course_ids(user))
        | Q(is_published=True, course__students=user)
    ).distinct()


def accessible_problems(user):
    """Problems a user may view — mirrors ``accessible_assignments`` one level down."""
    if user.is_staff:
        return Problem.objects.all()
    return Problem.objects.filter(
        Q(assignment__course_id__in=_staff_course_ids(user))
        | Q(assignment__is_published=True, assignment__course__students=user)
    ).distinct()


def editable_courses(user):
    """Courses a user may edit/manage: all (admin) or those they instruct."""
    if user.is_staff:
        return Course.objects.all()
    return Course.objects.filter(instructors=user)


def editable_assignments(user):
    if user.is_staff:
        return Assignment.objects.all()
    return Assignment.objects.filter(course__instructors=user)


def editable_problems(user):
    if user.is_staff:
        return Problem.objects.all()
    return Problem.objects.filter(assignment__course__instructors=user)

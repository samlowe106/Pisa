from .models import Course
from .selectors import is_student_anywhere


def roles(request):
    """Per-request role flags for templates (nav, buttons).

    - ``is_site_admin``: Django staff flag — runs the whole site, creates courses.
    - ``is_instructor_anywhere``: admin, or an instructor of at least one course.
    - ``is_course_staff_anywhere``: the above, or a TA of at least one course.
    - ``is_student_anywhere``: enrolled as a student in at least one course (gates the
      student-only Assignments page).
    """
    user = request.user
    if not user.is_authenticated:
        return {}
    is_admin = bool(user.is_staff)
    is_instructor_anywhere = (
        is_admin or Course.objects.filter(instructors=user).exists()
    )
    is_course_staff_anywhere = (
        is_instructor_anywhere or Course.objects.filter(tas=user).exists()
    )
    return {
        "is_site_admin": is_admin,
        "is_instructor_anywhere": is_instructor_anywhere,
        "is_course_staff_anywhere": is_course_staff_anywhere,
        "is_student_anywhere": is_student_anywhere(user),
    }

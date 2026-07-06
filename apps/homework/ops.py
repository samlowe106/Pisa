"""Course-level operations that span multiple models: renewing (cloning) a course into a new
term/section and walking a course's renewal lineage."""

from django.utils.text import slugify

from .models import Assignment, Course, Problem, ProblemBlock


def _unique_course_slug(*parts):
    """A unique course slug from the given parts (title/term/section), with a numeric suffix
    on collision."""
    base = slugify(" ".join(part for part in parts if part)) or "course"
    slug = base
    suffix = 2
    while Course.objects.filter(slug=slug).exists():
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def renew_course(course, *, term, section, created_by):
    """Deep-copy ``course`` into a new offering for ``term``/``section``: its assignments,
    problems, and blocks, re-linking the shared Lean source files. Carries over course settings
    and staff (instructors + TAs) but NOT students or submissions, and clears due dates. Records
    ``renewed_from`` for lineage. Wrap the call in a transaction.
    """
    new_course = Course.objects.create(
        title=course.title,
        slug=_unique_course_slug(course.title, term, section),
        description=course.description,
        scoring_method=course.scoring_method,
        thumbnail=course.thumbnail,
        thumbnail_preset=course.thumbnail_preset,
        is_active=True,
        grade_a_min=course.grade_a_min,
        grade_b_min=course.grade_b_min,
        grade_c_min=course.grade_c_min,
        grade_d_min=course.grade_d_min,
        term=term,
        section=section,
        renewed_from=course,
    )
    new_course.instructors.set(course.instructors.all())
    new_course.tas.set(course.tas.all())

    for assignment in course.assignments.all():
        new_assignment = Assignment.objects.create(
            course=new_course,
            title=assignment.title,
            slug=assignment.slug,  # unique per course; the new course is empty
            description=assignment.description,
            created_by=created_by,
            is_published=assignment.is_published,
            due_date=None,  # new term — instructor sets fresh due dates
        )
        new_assignment.source_files.set(assignment.source_files.all())

        for problem in assignment.problems.all():
            new_problem = Problem.objects.create(
                assignment=new_assignment,
                title=problem.title,
                statement=problem.statement,
                required_code=problem.required_code,
                grading_stub=problem.grading_stub,
                order=problem.order,
                points=problem.points,
                allowed_constructs=list(problem.allowed_constructs or []),
                axiom_target=problem.axiom_target,
                allowed_axioms=problem.allowed_axioms,
            )
            new_problem.visible_source_files.set(problem.visible_source_files.all())
            ProblemBlock.objects.bulk_create(
                [
                    ProblemBlock(
                        problem=new_problem,
                        block_type=block.block_type,
                        content=block.content,
                        order=block.order,
                    )
                    for block in problem.blocks.all()
                ]
            )

    return new_course


def course_family(course):
    """Every offering (section) in ``course``'s renew lineage — the root of the chain and all
    of its descendants — oldest first. A standalone course is a family of one."""
    root = course
    guard = 0
    while root.renewed_from_id and guard < 1000:
        root = root.renewed_from
        guard += 1
    family = []
    seen = set()
    queue = [root]
    while queue:
        current = queue.pop(0)
        if current.pk in seen:
            continue
        seen.add(current.pk)
        family.append(current)
        queue.extend(current.renewals.all())
    family.sort(key=lambda offering: offering.created_at)
    return family

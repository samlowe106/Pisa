import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.templatetags.static import static
from django.urls import reverse
from django.utils.text import slugify

User = get_user_model()

# Preset course thumbnails: drop image files into static/<this dir>/ and they appear in the
# course-form picker automatically.
THUMBNAIL_PRESET_DIR = "homework/img/thumbnails"
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".svg", ".webp", ".gif"}

# Slugs that would shadow literal segments in the nested course/assignment/problem URLs.
RESERVED_COURSE_SLUGS = {"create"}
RESERVED_ASSIGNMENT_SLUGS = {
    "edit",
    "enroll",
    "export",
    "assignments",
    "problems",
    "new",
    "create",
}


def validate_course_slug(value):
    if value in RESERVED_COURSE_SLUGS:
        raise ValidationError(f"'{value}' is a reserved slug; please choose another.")


def validate_assignment_slug(value):
    if value in RESERVED_ASSIGNMENT_SLUGS:
        raise ValidationError(f"'{value}' is a reserved slug; please choose another.")


class Course(models.Model):
    SCORING_BEST = "best"
    SCORING_RECENT = "recent"
    SCORING_SUPERSCORE = "superscore"
    SCORING_CHOICES = [
        (SCORING_BEST, "Best attempt"),
        (SCORING_RECENT, "Most recent submission"),
        (SCORING_SUPERSCORE, "Superscored"),
    ]

    title = models.CharField(max_length=255)
    slug = models.SlugField(
        max_length=100, unique=True, validators=[validate_course_slug]
    )
    description = models.TextField(blank=True)
    instructors = models.ManyToManyField(
        User, related_name="courses_instructing", blank=True
    )
    tas = models.ManyToManyField(User, related_name="courses_assisting", blank=True)
    students = models.ManyToManyField(User, related_name="courses_enrolled", blank=True)
    scoring_method = models.CharField(
        max_length=20,
        choices=SCORING_CHOICES,
        default=SCORING_BEST,
        help_text="How each student's score on a problem is derived from their submissions.",
    )
    # Thumbnail: an uploaded image, or the filename of a site-provided preset (see below).
    thumbnail = models.ImageField(upload_to="course_thumbnails/", blank=True)
    thumbnail_preset = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive courses are filed under “Previous courses” for students.",
    )
    # Per-course letter-grade cutoffs (minimum %); F is anything below the D cutoff.
    grade_a_min = models.PositiveSmallIntegerField(default=90)
    grade_b_min = models.PositiveSmallIntegerField(default=80)
    grade_c_min = models.PositiveSmallIntegerField(default=70)
    grade_d_min = models.PositiveSmallIntegerField(default=60)
    # Offering identity: which run of the course this is, and the offering it was renewed from
    # (its previous term/section) — see renew_course().
    term = models.CharField(max_length=60, blank=True, help_text="e.g. “Summer 2026”.")
    section = models.CharField(
        max_length=60, blank=True, help_text="e.g. “Section 002”."
    )
    renewed_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="renewals",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("homework:course_detail", kwargs={"slug": self.slug})

    @property
    def display_name(self):
        """Title qualified by term/section, e.g. “Intro to Lean (Summer 2026 · Section 2)”."""
        qualifier = " · ".join(part for part in (self.term, self.section) if part)
        return f"{self.title} ({qualifier})" if qualifier else self.title

    @property
    def thumbnail_url(self):
        """The course thumbnail URL: an uploaded image wins, else a chosen preset, else ''."""
        if self.thumbnail:
            return self.thumbnail.url
        if self.thumbnail_preset:
            return static(f"{THUMBNAIL_PRESET_DIR}/{self.thumbnail_preset}")
        return ""

    @property
    def thumbnail_credit(self):
        """Attribution dict for the chosen preset thumbnail; None for uploads or no preset."""
        if self.thumbnail or not self.thumbnail_preset:
            return None
        return _thumbnail_preset_attribution(self.thumbnail_preset) or None

    def grade_bands(self):
        """(minimum %, letter, css class) bands, highest first; F is below the D cutoff."""
        return [
            (self.grade_a_min, "A", "grade-a"),
            (self.grade_b_min, "B", "grade-b"),
            (self.grade_c_min, "C", "grade-c"),
            (self.grade_d_min, "D", "grade-d"),
        ]

    def letter_for(self, percent):
        """The (letter, css class) for a percentage under this course's grade bands."""
        for threshold, letter, css_class in self.grade_bands():
            if percent >= threshold:
                return letter, css_class
        return "F", "grade-f"

    # --- Per-course roles. Site admins (user.is_staff) outrank everyone in every course;
    # capability is hierarchical: admin ⊇ instructor ⊇ TA ⊇ student. ---

    def is_instructor(self, user):
        """Edit access: a site admin or one of this course's instructors. Instructors can
        edit content, export grades, and manage TAs/students."""
        return bool(user.is_staff) or self.instructors.filter(pk=user.pk).exists()

    def is_course_staff(self, user):
        """Staff-side *view* access (grades, drafts, all source files): admin, instructor,
        or TA. TAs get this but cannot edit or export."""
        return self.is_instructor(user) or self.tas.filter(pk=user.pk).exists()

    def can_manage_instructors(self, user):
        """Only site admins add or remove instructors."""
        return bool(user.is_staff)

    def role_of(self, user):
        """The user's effective role label, or None — for display only."""
        if user.is_staff:
            return "admin"
        if self.instructors.filter(pk=user.pk).exists():
            return "instructor"
        if self.tas.filter(pk=user.pk).exists():
            return "ta"
        if self.students.filter(pk=user.pk).exists():
            return "student"
        return None


class Assignment(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    source_files = models.ManyToManyField(
        "LeanSourceFile",
        blank=True,
        related_name="assignments",
    )
    title = models.CharField(max_length=255)
    # Unique per course (not globally), so different courses can reuse a slug like "hw1".
    slug = models.SlugField(max_length=100, validators=[validate_assignment_slug])
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ["course", "slug"]

    def __str__(self):
        return f"{self.course.title}: {self.title}"

    def get_absolute_url(self):
        return reverse(
            "homework:assignment_detail",
            kwargs={"course_slug": self.course.slug, "assignment_slug": self.slug},
        )


class LeanSourceFile(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    content = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="lean_source_files",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title


class Problem(models.Model):
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="problems"
    )
    visible_source_files = models.ManyToManyField(
        "LeanSourceFile",
        blank=True,
        related_name="visible_in_problems",
        help_text=(
            "Imported source files students can view on this problem. Others stay hidden "
            "but are still compiled into submissions."
        ),
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional display name shown to students; defaults to its position, e.g. Problem 3.",
    )
    statement = models.TextField(blank=True)
    required_code = models.TextField(
        blank=True,
        default="",
        help_text="A code snippet that must remain in student submissions.",
    )
    grading_stub = models.TextField(
        blank=True,
        default="",
        help_text="Optional Lean code appended to submissions for grading.",
    )
    order = models.PositiveIntegerField(default=0)
    points = models.PositiveIntegerField(
        default=1, help_text="Points awarded for solving this problem"
    )
    # --- Submission policy (see apps/homework/lean_policy.py) ---
    allowed_constructs = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Disallowed Lean constructs (sorry, axiom, IO, #eval, …) to re-permit for this "
            "problem. Empty = enforce the full blacklist."
        ),
    )
    axiom_target = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Declaration to audit with `#print axioms` after a successful compile (e.g. the "
            "theorem the student proves). Blank = skip the axiom check."
        ),
    )
    allowed_axioms = models.CharField(
        max_length=500,
        blank=True,
        help_text=(
            "Comma-separated extra axioms the proof may depend on, beyond Lean's standard "
            "sound ones (propext, Classical.choice, Quot.sound) — e.g. axioms this problem "
            "provides."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.assignment.title}: {self.display_name}"

    @property
    def position(self):
        """1-based index of this problem within its assignment (the URL number).

        Iterates the related manager (rather than ``values_list``) so a caller that
        ``prefetch_related("problems")`` pays no query here; otherwise it's one query.
        """
        for index, problem in enumerate(self.assignment.problems.all(), start=1):
            if problem.pk == self.pk:
                return index
        return 1

    @property
    def display_name(self):
        """Human-facing name: the optional title, else a lazy "Problem N" by position.

        Resolved on access rather than stored, so it never goes stale when problems are
        reordered. In ordered template loops prefer ``forloop.counter`` to avoid the extra
        position query this does.
        """
        if self.title:
            return self.title
        if self.pk:
            return f"Problem {self.position}"
        return "Problem"

    def get_absolute_url(self):
        return reverse(
            "homework:problem_detail",
            kwargs={
                "course_slug": self.assignment.course.slug,
                "assignment_slug": self.assignment.slug,
                "number": self.position,
            },
        )


class ProblemBlock(models.Model):
    BLOCK_TYPE_TEXT = "text"
    BLOCK_TYPE_FIXED_CODE = "fixed_code"
    BLOCK_TYPE_EDITABLE_CODE = "editable_code"

    BLOCK_TYPE_CHOICES = [
        (BLOCK_TYPE_TEXT, "Plain Text"),
        (BLOCK_TYPE_FIXED_CODE, "Fixed Code"),
        (BLOCK_TYPE_EDITABLE_CODE, "Editable Code"),
    ]

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="blocks"
    )
    block_type = models.CharField(
        max_length=20, choices=BLOCK_TYPE_CHOICES, default=BLOCK_TYPE_EDITABLE_CODE
    )
    content = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = ["problem", "order"]

    def __str__(self):
        return f"{self.problem.title} - {self.get_block_type_display()} (#{self.order})"


class Submission(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PASSED = "passed"
    STATUS_FAILED = "failed"
    STATUS_ERROR = "error"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PASSED, "Passed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_ERROR, "Error"),
    ]

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="submissions"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submissions")
    code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    result = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return (
            f"Submission {self.pk} for {self.problem.display_name} "
            f"by {self.user.username}"
        )

    @property
    def is_late(self):
        """Submitted after the assignment's due date (if one is set)."""
        due = self.problem.assignment.due_date
        return bool(due and self.created_at and self.created_at > due)


def _staff_course_ids(user):
    """Ids of courses where the user is instructor or TA (admins handled separately)."""
    return list(
        Course.objects.filter(
            models.Q(instructors=user) | models.Q(tas=user)
        ).values_list("id", flat=True)
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
        models.Q(course_id__in=_staff_course_ids(user))
        | models.Q(is_published=True, course__students=user)
    ).distinct()


def accessible_problems(user):
    """Problems a user may view — mirrors ``accessible_assignments`` one level down."""
    if user.is_staff:
        return Problem.objects.all()
    return Problem.objects.filter(
        models.Q(assignment__course_id__in=_staff_course_ids(user))
        | models.Q(assignment__is_published=True, assignment__course__students=user)
    ).distinct()


def editable_courses(user):
    """Courses a user may edit/manage: all (admin) or those they instruct."""
    if user.is_staff:
        return Course.objects.all()
    return Course.objects.filter(instructors=user)


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


def editable_assignments(user):
    if user.is_staff:
        return Assignment.objects.all()
    return Assignment.objects.filter(course__instructors=user)


def editable_problems(user):
    if user.is_staff:
        return Problem.objects.all()
    return Problem.objects.filter(assignment__course__instructors=user)


def _thumbnail_preset_attribution(key):
    """Attribution for a preset image, read from its ``<stem>.json`` sidecar (``{}`` if none).

    The sidecar may contain any of: title, author, author_url, license, license_url, source_url.
    """
    from django.conf import settings

    stem = Path(key).stem
    for base in settings.STATICFILES_DIRS:
        sidecar = Path(base) / THUMBNAIL_PRESET_DIR / f"{stem}.json"
        if sidecar.is_file():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                return {}
    return {}


def available_thumbnail_presets():
    """Preset thumbnails the site provides. Drop image files into
    ``static/homework/img/thumbnails/`` and they appear here automatically (keyed by filename);
    an optional ``<name>.json`` sidecar next to each image carries its attribution.
    """
    from django.conf import settings

    presets = []
    seen = set()
    for base in settings.STATICFILES_DIRS:
        directory = Path(base) / THUMBNAIL_PRESET_DIR
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in THUMBNAIL_EXTENSIONS and path.name not in seen:
                seen.add(path.name)
                presets.append(
                    {
                        "key": path.name,
                        "label": path.stem.replace("-", " ").replace("_", " ").title(),
                        "url": static(f"{THUMBNAIL_PRESET_DIR}/{path.name}"),
                        "attribution": _thumbnail_preset_attribution(path.name),
                    }
                )
    return presets

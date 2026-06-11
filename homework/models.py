from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Course(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    instructor = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="courses_taught"
    )
    students = models.ManyToManyField(User, related_name="courses_enrolled", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


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
    slug = models.SlugField(max_length=100, unique=True)
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


class LeanSourceFile(models.Model):
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    content = models.TextField(blank=True)
    visible = models.BooleanField(
        default=True,
        help_text="Visible to students when imported into an assignment.",
    )
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
    title = models.CharField(max_length=255)
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.assignment.title}: {self.title}"


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
        return f"Submission {self.pk} for {self.problem.title} by {self.user.username}"

from django.contrib import admin

from .models import Assignment, Problem, Submission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "created_by",
        "is_published",
        "due_date",
        "created_at",
    )
    prepopulated_fields = {"slug": ("title",)}
    list_filter = ("is_published", "created_at")
    search_fields = ("title", "description")


@admin.register(Problem)
class ProblemAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "assignment",
        "order",
        "created_at",
    )
    list_filter = ("assignment",)
    search_fields = ("title", "statement")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("problem", "user", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("problem__title", "user__username", "result")

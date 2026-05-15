from django import forms
from django.contrib.auth import get_user_model

from .models import Assignment, Course, Problem, ProblemBlock

User = get_user_model()


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["title", "slug", "description", "students"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "students": forms.SelectMultiple(attrs={"size": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["students"].queryset = User.objects.filter(is_staff=False)


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["course", "title", "slug", "description", "due_date", "is_published"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class ProblemForm(forms.ModelForm):
    class Meta:
        model = Problem
        fields = [
            "title",
            "statement",
            "required_code",
            "grading_stub",
            "points",
            "order",
        ]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 4}),
            "required_code": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
            "grading_stub": forms.Textarea(attrs={"rows": 6, "class": "mono"}),
            "points": forms.NumberInput(attrs={"type": "number", "min": "1"}),
        }


class ProblemBlockForm(forms.ModelForm):
    class Meta:
        model = ProblemBlock
        fields = ["block_type", "content", "order"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 8, "class": "mono"}),
            "order": forms.NumberInput(attrs={"type": "number", "min": "0"}),
        }

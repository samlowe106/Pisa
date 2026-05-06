from django import forms

from .models import Assignment, Problem


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["title", "slug", "description", "due_date", "is_published"]
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
            "starter_code",
            "required_code",
            "grading_stub",
            "order",
        ]
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 4}),
            "starter_code": forms.Textarea(attrs={"rows": 14, "class": "mono"}),
            "required_code": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
            "grading_stub": forms.Textarea(attrs={"rows": 6, "class": "mono"}),
        }

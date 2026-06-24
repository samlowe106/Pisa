from django import forms
from django.contrib.auth import get_user_model

from . import lean_policy
from .models import (
    Assignment,
    Course,
    LeanSourceFile,
    Problem,
    ProblemBlock,
    available_thumbnail_presets,
)

User = get_user_model()


class EmailRosterField(forms.CharField):
    """A comma-separated list of emails that resolves to existing User objects.

    Whitespace around each email is ignored; unknown emails raise a validation error.
    """

    widget = forms.TextInput(
        attrs={"placeholder": "alice@example.com, bob@example.com"}
    )

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, value):
        raw = super().clean(value) or ""
        emails = [part.strip() for part in raw.split(",") if part.strip()]
        users, seen, unknown = [], set(), []
        for email in emails:
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                unknown.append(email)
            elif user.pk not in seen:
                seen.add(user.pk)
                users.append(user)
        if unknown:
            raise forms.ValidationError("No account found for: " + ", ".join(unknown))
        return users

    def prepare_value(self, value):
        # On edit, `value` is the list/queryset of current members -> show their emails.
        if value and not isinstance(value, str):
            return ", ".join(u.email for u in value if getattr(u, "email", ""))
        return value


class CourseForm(forms.ModelForm):
    field_order = [
        "title",
        "slug",
        "description",
        "instructors",
        "tas",
        "students",
        "scoring_method",
        "grade_a_min",
        "grade_b_min",
        "grade_c_min",
        "grade_d_min",
        "is_active",
    ]
    instructors = EmailRosterField(
        label="Instructors",
        help_text="Emails, comma-separated. They edit content and manage TAs & students.",
    )
    tas = EmailRosterField(
        label="Teaching assistants",
        help_text="Emails, comma-separated. They see grades and all source files, but can't edit.",
    )
    students = EmailRosterField(label="Students", help_text="Emails, comma-separated.")
    thumbnail_preset = forms.ChoiceField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Course
        fields = [
            "title",
            "slug",
            "description",
            "scoring_method",
            "grade_a_min",
            "grade_b_min",
            "grade_c_min",
            "grade_d_min",
            "is_active",
            "thumbnail",
            "thumbnail_preset",
        ]
        labels = {
            "scoring_method": "Grade scoring",
            "grade_a_min": "A",
            "grade_b_min": "B",
            "grade_c_min": "C",
            "grade_d_min": "D",
            "is_active": "Active course",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "thumbnail": forms.ClearableFileInput(attrs={"accept": "image/*"}),
            "grade_a_min": forms.NumberInput(attrs={"min": 0, "max": 100}),
            "grade_b_min": forms.NumberInput(attrs={"min": 0, "max": 100}),
            "grade_c_min": forms.NumberInput(attrs={"min": 0, "max": 100}),
            "grade_d_min": forms.NumberInput(attrs={"min": 0, "max": 100}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.presets = available_thumbnail_presets()
        self.fields["thumbnail_preset"].choices = [("", "")] + [
            (p["key"], p["label"]) for p in self.presets
        ]
        if self.instance and self.instance.pk:
            self.fields["instructors"].initial = list(self.instance.instructors.all())
            self.fields["tas"].initial = list(self.instance.tas.all())
            self.fields["students"].initial = list(self.instance.students.all())
        # Only site admins may set instructors.
        if not (user and user.is_staff):
            self.fields.pop("instructors", None)

    def clean(self):
        cleaned = super().clean()
        # A user may hold only one role in a course.
        role_of = {}
        for role in ("instructors", "tas", "students"):
            for member in cleaned.get(role) or []:
                if member.pk in role_of and role_of[member.pk] != role:
                    self.add_error(
                        role,
                        f"{member.email} is also listed under {role_of[member.pk]}.",
                    )
                role_of[member.pk] = role
        # An upload wins over a preset — don't persist a stale preset alongside it.
        if cleaned.get("thumbnail") and cleaned.get("thumbnail_preset"):
            cleaned["thumbnail_preset"] = ""
        # Grade cutoffs must strictly descend (A > B > C > D).
        bands = [
            cleaned.get("grade_a_min"),
            cleaned.get("grade_b_min"),
            cleaned.get("grade_c_min"),
            cleaned.get("grade_d_min"),
        ]
        if all(b is not None for b in bands) and not (
            bands[0] > bands[1] > bands[2] > bands[3]
        ):
            self.add_error(None, "Grade cutoffs must strictly descend: A > B > C > D.")
        return cleaned

    def apply_rosters(self, course):
        """Write the M2M rosters from cleaned data (instructors only when the field exists)."""
        if "instructors" in self.cleaned_data:
            course.instructors.set(self.cleaned_data["instructors"])
        course.tas.set(self.cleaned_data.get("tas", []))
        course.students.set(self.cleaned_data.get("students", []))


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = [
            "course",
            "title",
            "slug",
            "description",
            "due_date",
            "is_published",
            "source_files",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "due_date": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "source_files": forms.SelectMultiple(attrs={"size": 6}),
        }


class LeanSourceFileForm(forms.ModelForm):
    class Meta:
        model = LeanSourceFile
        fields = ["title", "slug", "content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 12, "class": "mono"}),
        }


class ProblemForm(forms.ModelForm):
    # Which blacklisted constructs to re-permit for this problem (stored on the JSONField).
    allowed_constructs = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        choices=[
            (rule.id, f"{rule.id} ({rule.category}) — {rule.reason}")
            for rule in lean_policy.RULES
        ],
        label="Allowed constructs",
        help_text="Checked constructs are permitted here despite the global blacklist.",
    )

    class Meta:
        model = Problem
        fields = [
            "title",
            "statement",
            "required_code",
            "grading_stub",
            "points",
            "order",
            "visible_source_files",
            "allowed_constructs",
            "axiom_target",
            "allowed_axioms",
        ]
        labels = {
            "title": "Name",
            "visible_source_files": "Source files visible to students",
        }
        help_texts = {
            "title": "Optional. Shown to students; defaults to “Problem N” (its position).",
            "visible_source_files": (
                "Only this assignment's imported files are listed. Unchecked files stay "
                "hidden from students but are still compiled into submissions."
            ),
        }
        widgets = {
            "statement": forms.Textarea(attrs={"rows": 4}),
            "required_code": forms.Textarea(attrs={"rows": 4, "class": "mono"}),
            "grading_stub": forms.Textarea(attrs={"rows": 6, "class": "mono"}),
            "points": forms.NumberInput(attrs={"type": "number", "min": "1"}),
            "visible_source_files": forms.CheckboxSelectMultiple(),
        }


class ProblemBlockForm(forms.ModelForm):
    class Meta:
        model = ProblemBlock
        fields = ["block_type", "content", "order"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 8, "class": "mono"}),
            "order": forms.NumberInput(attrs={"type": "number", "min": "0"}),
        }


class CourseRenewForm(forms.Form):
    """Term/section for a renewed (cloned) course offering."""

    term = forms.CharField(max_length=60, required=False, help_text="e.g. Summer 2026")
    section = forms.CharField(
        max_length=60, required=False, help_text="e.g. Section 002"
    )

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("term") or cleaned.get("section")):
            raise forms.ValidationError(
                "Enter a term and/or section so the new offering is distinguishable."
            )
        return cleaned

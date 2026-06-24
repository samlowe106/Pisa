"""Form validation: course grade-band/roster rules, renew term/section requirement, and the
reserved-slug / policy-field handling on assignment and problem forms."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.homework.forms import (
    AssignmentForm,
    CourseForm,
    CourseRenewForm,
    ProblemForm,
)
from apps.homework.models import Course

User = get_user_model()


def _course_data(**overrides):
    data = {
        "title": "Course",
        "slug": "a-course",
        "description": "",
        "scoring_method": Course.SCORING_BEST,
        "grade_a_min": 90,
        "grade_b_min": 80,
        "grade_c_min": 70,
        "grade_d_min": 60,
        "is_active": True,
        "instructors": "",
        "tas": "",
        "students": "",
        "thumbnail_preset": "",
    }
    data.update(overrides)
    return data


class CourseFormTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin", is_staff=True)
        self.alice = User.objects.create_user("alice", email="alice@example.com")
        self.bob = User.objects.create_user("bob", email="bob@example.com")

    def test_valid_form(self):
        form = CourseForm(data=_course_data(), user=self.admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_grade_bands_must_strictly_descend(self):
        form = CourseForm(data=_course_data(grade_b_min=95), user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertTrue(any("strictly descend" in e for e in form.non_field_errors()))

    def test_roster_resolves_known_emails(self):
        form = CourseForm(
            data=_course_data(students="alice@example.com, bob@example.com"),
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.cleaned_data["students"]), {self.alice, self.bob})

    def test_unknown_email_is_rejected(self):
        form = CourseForm(
            data=_course_data(students="ghost@example.com"), user=self.admin
        )
        self.assertFalse(form.is_valid())
        self.assertIn("students", form.errors)

    def test_a_user_may_hold_only_one_role(self):
        form = CourseForm(
            data=_course_data(tas="alice@example.com", students="alice@example.com"),
            user=self.admin,
        )
        self.assertFalse(form.is_valid())

    def test_instructors_field_is_admin_only(self):
        instructor = User.objects.create_user("instr")
        self.assertNotIn("instructors", CourseForm(user=instructor).fields)
        self.assertIn("instructors", CourseForm(user=self.admin).fields)


class CourseRenewFormTests(TestCase):
    def test_requires_term_or_section(self):
        self.assertFalse(CourseRenewForm(data={"term": "", "section": ""}).is_valid())

    def test_either_field_suffices(self):
        self.assertTrue(
            CourseRenewForm(data={"term": "Fall 2026", "section": ""}).is_valid()
        )
        self.assertTrue(CourseRenewForm(data={"term": "", "section": "002"}).is_valid())


class AssignmentFormTests(TestCase):
    def setUp(self):
        self.course = Course.objects.create(title="C", slug="c")

    def _data(self, slug):
        return {
            "course": self.course.pk,
            "title": "HW",
            "slug": slug,
            "description": "",
            "is_published": True,
        }

    def test_reserved_slug_is_rejected(self):
        form = AssignmentForm(data=self._data("edit"))
        self.assertFalse(form.is_valid())
        self.assertIn("slug", form.errors)

    def test_ordinary_slug_is_accepted(self):
        self.assertTrue(AssignmentForm(data=self._data("hw2")).is_valid())


class ProblemFormTests(TestCase):
    def test_allowed_constructs_accepts_known_rule_ids(self):
        form = ProblemForm(
            data={"points": 1, "order": 0, "allowed_constructs": ["sorry"]}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["allowed_constructs"], ["sorry"])

    def test_allowed_constructs_rejects_unknown_id(self):
        form = ProblemForm(
            data={"points": 1, "order": 0, "allowed_constructs": ["not-a-rule"]}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("allowed_constructs", form.errors)

    def test_minimal_problem_is_valid(self):
        self.assertTrue(ProblemForm(data={"points": 1, "order": 0}).is_valid())


class CourseFormThumbnailAndDedupTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("admin2", is_staff=True)
        self.alice = User.objects.create_user("alice2", email="alice2@example.com")

    def test_duplicate_email_in_a_roster_is_collapsed(self):
        form = CourseForm(
            data=_course_data(students="alice2@example.com, alice2@example.com"),
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["students"], [self.alice])  # deduped

    def test_upload_clears_a_chosen_preset(self):
        from io import BytesIO

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (1, 1)).save(buffer, "PNG")
        upload = SimpleUploadedFile(
            "t.png", buffer.getvalue(), content_type="image/png"
        )

        form = CourseForm(
            data=_course_data(thumbnail_preset="aurora.svg"),
            files={"thumbnail": upload},
            user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["thumbnail_preset"], "")  # upload wins

"""Permission / infosec tests for the per-course role matrix.

The headline concern: grade export is instructor/admin-only at the *endpoint* (TAs can view
grades but must not be able to mass-export them).
"""

from django.test import TestCase
from django.urls import reverse

from apps.homework.models import (
    Assignment,
    accessible_assignments,
    editable_courses,
)

from .utils import make_role_matrix


class ExportGradesPermissionTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.csv = reverse(
            "homework:export_grades_csv", kwargs={"course_slug": "test-course"}
        )
        self.xlsx = reverse(
            "homework:export_grades_excel", kwargs={"course_slug": "test-course"}
        )

    def _status(self, who, url):
        self.client.force_login(self.m[who])
        return self.client.get(url).status_code

    def test_instructor_and_admin_can_export(self):
        for who in ("admin", "instructor"):
            self.assertEqual(self._status(who, self.csv), 200, who)
            self.assertEqual(self._status(who, self.xlsx), 200, who)

    def test_ta_student_outsider_cannot_export(self):
        for who in ("ta", "student", "outsider"):
            self.assertEqual(self._status(who, self.csv), 403, who)
            self.assertEqual(self._status(who, self.xlsx), 403, who)

    def test_unauthenticated_is_redirected_not_403(self):
        self.assertEqual(self.client.get(self.csv).status_code, 302)

    def test_ta_sees_grades_but_no_export_control(self):
        self.client.force_login(self.m["ta"])
        html = self.client.get(
            reverse("homework:course_detail", kwargs={"slug": "test-course"})
        ).content.decode()
        self.assertIn("students-table", html)  # TAs can view grades
        self.assertNotIn("export-grades-btn", html)  # but not export them


class CoursePermissionMatrixTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def _get(self, who, url):
        self.client.force_login(self.m[who])
        return self.client.get(url).status_code

    def test_edit_and_renew_are_instructor_admin_only(self):
        for name in ("course_update", "course_renew"):
            url = reverse(f"homework:{name}", kwargs={"slug": "test-course"})
            self.assertEqual(self._get("admin", url), 200, name)
            self.assertEqual(self._get("instructor", url), 200, name)
            for who in ("ta", "student", "outsider"):
                self.assertEqual(self._get(who, url), 404, f"{name}/{who}")

    def test_course_create_is_admin_only(self):
        url = reverse("homework:course_create")
        self.assertEqual(self._get("admin", url), 200)
        for who in ("instructor", "ta", "student"):
            self.assertEqual(self._get(who, url), 403, who)

    def test_only_admins_manage_instructors(self):
        url = reverse("homework:course_add_member", kwargs={"slug": "test-course"})
        data = {"role": "instructor", "identifier": self.m["outsider"].username}
        self.client.force_login(self.m["admin"])
        self.assertEqual(
            self.client.post(url, data).status_code, 302
        )  # success redirect
        self.assertTrue(
            self.m["course"].instructors.filter(pk=self.m["outsider"].pk).exists()
        )
        # A non-admin instructor cannot add another instructor.
        self.client.force_login(self.m["instructor"])
        self.assertEqual(self.client.post(url, data).status_code, 403)

    def test_tas_cannot_add_students(self):
        url = reverse("homework:course_add_member", kwargs={"slug": "test-course"})
        data = {"role": "student", "identifier": self.m["outsider"].username}
        self.client.force_login(self.m["ta"])
        self.assertEqual(self.client.post(url, data).status_code, 403)
        # ...but an instructor can.
        self.client.force_login(self.m["instructor"])
        self.assertEqual(self.client.post(url, data).status_code, 302)


class VisibilityHelperTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        Assignment.objects.create(
            course=self.m["course"],
            title="Draft",
            slug="draft",
            created_by=self.m["instructor"],
            is_published=False,
        )

    def test_students_dont_see_draft_assignments(self):
        visible = accessible_assignments(self.m["student"]).filter(
            course=self.m["course"]
        )
        slugs = set(visible.values_list("slug", flat=True))
        self.assertIn("hw1", slugs)
        self.assertNotIn("draft", slugs)

    def test_staff_see_drafts(self):
        for who in ("instructor", "ta", "admin"):
            slugs = set(
                accessible_assignments(self.m[who])
                .filter(course=self.m["course"])
                .values_list("slug", flat=True)
            )
            self.assertIn("draft", slugs, who)

    def test_editable_courses_scoping(self):
        self.assertIn(self.m["course"], editable_courses(self.m["instructor"]))
        self.assertIn(self.m["course"], editable_courses(self.m["admin"]))
        self.assertNotIn(self.m["course"], editable_courses(self.m["ta"]))
        self.assertNotIn(self.m["course"], editable_courses(self.m["student"]))

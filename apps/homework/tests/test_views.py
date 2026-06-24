"""View-level integration smoke tests: course CRUD, the renew flow, enrolment, member
management, grade-export *content*, nested assignment/problem resolution, problem reorder, and
the Lean source-file library. Permission edges live in test_permissions.py; these confirm the
happy paths and a few key 404s."""

import json
from io import BytesIO

import openpyxl
from django.test import TestCase
from django.urls import reverse

from apps.homework.models import Course, LeanSourceFile, Problem, Submission

from .utils import make_role_matrix


def _course_post(**overrides):
    data = {
        "title": "New Course",
        "slug": "new-course",
        "description": "",
        "scoring_method": Course.SCORING_BEST,
        "grade_a_min": 90,
        "grade_b_min": 80,
        "grade_c_min": 70,
        "grade_d_min": 60,
        "is_active": "on",
        "instructors": "",
        "tas": "",
        "students": "",
        "thumbnail_preset": "",
    }
    data.update(overrides)
    return data


class DashboardAndListTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def test_dashboard_redirects_to_course_list(self):
        self.client.force_login(self.m["student"])
        response = self.client.get(reverse("homework:dashboard"))
        self.assertRedirects(response, reverse("homework:course_list"))

    def test_course_list_renders_for_logged_in_user(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(
            self.client.get(reverse("homework:course_list")).status_code, 200
        )

    def test_unauthenticated_is_redirected_to_login(self):
        response = self.client.get(reverse("homework:course_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class CourseCRUDTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def test_admin_creates_course_and_becomes_instructor(self):
        self.client.force_login(self.m["admin"])
        response = self.client.post(reverse("homework:course_create"), _course_post())
        self.assertRedirects(response, reverse("homework:course_list"))
        course = Course.objects.get(slug="new-course")
        self.assertIn(self.m["admin"], course.instructors.all())

    def test_instructor_updates_course_title(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.post(
            reverse("homework:course_update", kwargs={"slug": "test-course"}),
            _course_post(title="Renamed", slug="test-course"),
        )
        self.assertEqual(response.status_code, 302)
        self.m["course"].refresh_from_db()
        self.assertEqual(self.m["course"].title, "Renamed")

    def test_renew_creates_a_new_offering(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.post(
            reverse("homework:course_renew", kwargs={"slug": "test-course"}),
            {"term": "Fall 2026", "section": ""},
        )
        self.assertEqual(response.status_code, 302)
        renewed = Course.objects.get(renewed_from=self.m["course"])
        self.assertEqual(renewed.term, "Fall 2026")
        self.assertRedirects(
            response,
            reverse("homework:course_detail", kwargs={"slug": renewed.slug}),
        )


class EnrolAndMembershipTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def test_outsider_can_self_enrol(self):
        self.client.force_login(self.m["outsider"])
        response = self.client.post(
            reverse("homework:course_enroll", kwargs={"slug": "test-course"})
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.m["outsider"], self.m["course"].students.all())

    def test_course_staff_cannot_self_enrol(self):
        self.client.force_login(self.m["ta"])
        response = self.client.post(
            reverse("homework:course_enroll", kwargs={"slug": "test-course"})
        )
        self.assertEqual(response.status_code, 400)

    def test_instructor_removes_a_student(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.post(
            reverse("homework:course_remove_member", kwargs={"slug": "test-course"}),
            {"role": "student", "user_id": self.m["student"].pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(self.m["student"], self.m["course"].students.all())


class ExportContentTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        Submission.objects.create(
            problem=self.m["problem"],
            user=self.m["student"],
            code="x",
            status=Submission.STATUS_PASSED,
        )

    def test_csv_has_a_row_for_the_submission(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.get(
            reverse("homework:export_grades_csv", kwargs={"course_slug": "test-course"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("t_student", body)
        self.assertIn("Test Course", body)
        self.assertIn("Passed", body)

    def test_excel_has_a_header_and_data_row(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.get(
            reverse(
                "homework:export_grades_excel", kwargs={"course_slug": "test-course"}
            )
        )
        self.assertEqual(response.status_code, 200)
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        rows = list(workbook.active.iter_rows(values_only=True))
        self.assertEqual(rows[0][0], "Student")
        self.assertTrue(any("t_student" in str(cell) for cell in rows[1]))


class NestedResolutionTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.p2 = Problem.objects.create(
            assignment=self.m["assignment"], title="P2", points=1, order=1
        )

    def test_assignment_and_problem_pages_resolve(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(self.client.get("/courses/test-course/hw1/").status_code, 200)
        self.assertEqual(
            self.client.get("/courses/test-course/hw1/1/").status_code, 200
        )

    def test_problem_number_out_of_range_is_404(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(
            self.client.get("/courses/test-course/hw1/99/").status_code, 404
        )

    def test_unknown_assignment_is_404(self):
        self.client.force_login(self.m["student"])
        self.assertEqual(self.client.get("/courses/test-course/nope/").status_code, 404)

    def test_assignment_create_page_renders_for_instructor(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.get(
            reverse(
                "homework:assignment_create_for_course",
                kwargs={"course_slug": "test-course"},
            )
        )
        self.assertEqual(response.status_code, 200)


class ProblemReorderTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.p1 = self.m["problem"]
        self.p2 = Problem.objects.create(
            assignment=self.m["assignment"], title="P2", points=1, order=1
        )
        self.url = reverse(
            "homework:problem_reorder",
            kwargs={"course_slug": "test-course", "assignment_slug": "hw1"},
        )

    def test_reorder_persists_new_order(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.post(
            self.url,
            data=json.dumps({"order": [self.p2.pk, self.p1.pk]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.p1.refresh_from_db()
        self.p2.refresh_from_db()
        self.assertEqual(self.p2.order, 0)
        self.assertEqual(self.p1.order, 1)

    def test_incomplete_order_is_rejected(self):
        self.client.force_login(self.m["instructor"])
        response = self.client.post(
            self.url,
            data=json.dumps({"order": [self.p1.pk]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class LeanSourceFileViewTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()

    def test_library_is_instructor_or_admin_only(self):
        url = reverse("homework:lean_source_file_list")
        self.client.force_login(self.m["ta"])
        self.assertEqual(self.client.get(url).status_code, 403)
        for who in ("instructor", "admin"):
            self.client.force_login(self.m[who])
            self.assertEqual(self.client.get(url).status_code, 200, who)

    def test_create_page_renders(self):
        self.client.force_login(self.m["instructor"])
        self.assertEqual(
            self.client.get(reverse("homework:lean_source_file_create")).status_code,
            200,
        )

    def test_edit_is_scoped_to_the_owner(self):
        source = LeanSourceFile.objects.create(
            title="Mine", slug="mine", created_by=self.m["instructor"]
        )
        url = reverse("homework:lean_source_file_update", kwargs={"pk": source.pk})
        self.client.force_login(self.m["instructor"])
        self.assertEqual(self.client.get(url).status_code, 200)
        # A different instructor (here: the admin) can't edit someone else's file.
        self.client.force_login(self.m["admin"])
        self.assertEqual(self.client.get(url).status_code, 404)

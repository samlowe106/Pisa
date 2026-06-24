"""Smoke test for the development-data seeding command."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.homework.models import Assignment, Course, Problem, Submission

User = get_user_model()


class CreateTestDataCommandTests(TestCase):
    def test_seeds_data_and_is_idempotent(self):
        call_command("create_test_data", stdout=StringIO())
        self.assertTrue(User.objects.exists())
        self.assertTrue(Course.objects.exists())
        self.assertTrue(Assignment.objects.exists())
        self.assertTrue(Problem.objects.exists())
        self.assertTrue(Submission.objects.exists())

        counts = (User.objects.count(), Course.objects.count())
        # Re-running tops up rather than duplicating.
        call_command("create_test_data", stdout=StringIO())
        self.assertEqual((User.objects.count(), Course.objects.count()), counts)

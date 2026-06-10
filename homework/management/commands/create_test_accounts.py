"""Django management command to create test user and teacher accounts."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from homework.models import Assignment, Course, Problem, ProblemBlock

User = get_user_model()


class Command(BaseCommand):
    help = "Create test user and teacher accounts"

    def handle(self, *args, **options):
        # Create test teacher if not exists
        teacher, created = User.objects.get_or_create(
            username="teacher",
            defaults={"email": "teacher@example.com", "is_staff": True},
        )
        if created:
            teacher.set_password("password")
            teacher.save()
            self.stdout.write(
                self.style.SUCCESS(
                    "✓ Created teacher account: username=teacher, password=password"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("✓ Teacher account already exists"))

        # Create test student if not exists
        student, created = User.objects.get_or_create(
            username="student", defaults={"email": "student@example.com"}
        )
        if created:
            student.set_password("password")
            student.save()
            self.stdout.write(
                self.style.SUCCESS(
                    "✓ Created student account: username=student, password=password"
                )
            )
        else:
            self.stdout.write(self.style.WARNING("✓ Student account already exists"))

        self.stdout.write(self.style.SUCCESS("\n✓ Test accounts ready!"))

        # Create a default course, assignment, and problem for testing
        course, created = Course.objects.get_or_create(
            slug="intro-lean",
            defaults={
                "title": "Intro to Lean",
                "description": "A small test course",
                "instructor": teacher,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("✓ Created course: Intro to Lean"))
        else:
            self.stdout.write(
                self.style.WARNING("✓ Course Intro to Lean already exists")
            )

        # Enroll the test student
        if student not in course.students.all():
            course.students.add(student)
            self.stdout.write(self.style.SUCCESS("✓ Enrolled student in Intro to Lean"))

        assignment, created = Assignment.objects.get_or_create(
            course=course,
            slug="test-assignment",
            defaults={
                "title": "Test Assignment",
                "description": "Automatically-created assignment for testing",
                "created_by": teacher,
                "is_published": True,
            },
        )
        if created:
            self.stdout.write(
                self.style.SUCCESS("✓ Created assignment: Test Assignment")
            )
        else:
            self.stdout.write(
                self.style.WARNING("✓ Assignment Test Assignment already exists")
            )

        problem, created = Problem.objects.get_or_create(
            assignment=assignment,
            title="Hello Lean",
            defaults={
                "statement": "Write a simple Lean definition.",
                "required_code": "",
                "grading_stub": "",
                "order": 1,
                "points": 1,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("✓ Created problem: Hello Lean"))
        else:
            self.stdout.write(self.style.WARNING("✓ Problem Hello Lean already exists"))

        # Create example blocks if none exist
        if not problem.blocks.exists():
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_TEXT,
                content="Define a function that returns 0.",
                order=0,
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_FIXED_CODE,
                content="-- non-editable helper\nnamespace MyLib\nend\n",
                order=1,
            )
            ProblemBlock.objects.create(
                problem=problem,
                block_type=ProblemBlock.BLOCK_TYPE_EDITABLE_CODE,
                content="def my_zero : Nat := 0\n",
                order=2,
            )
            self.stdout.write(
                self.style.SUCCESS("✓ Added example blocks to Hello Lean")
            )

        self.stdout.write(
            self.style.SUCCESS(
                "\n✓ Default course/assignment/problem ready for testing"
            )
        )

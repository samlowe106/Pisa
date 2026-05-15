"""Django management command to create test user and teacher accounts."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

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

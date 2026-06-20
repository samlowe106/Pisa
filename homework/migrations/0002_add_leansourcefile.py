from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("homework", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LeanSourceFile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                ("content", models.TextField(blank=True)),
                (
                    "visible",
                    models.BooleanField(
                        default=True,
                        help_text="Visible to students when imported into an assignment.",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="lean_source_files",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["title"],
            },
        ),
        migrations.AddField(
            model_name="assignment",
            name="source_files",
            field=models.ManyToManyField(
                blank=True,
                related_name="assignments",
                to="homework.leansourcefile",
            ),
        ),
    ]

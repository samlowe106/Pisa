from django.apps import AppConfig


class HomeworkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Import path of the app (now under apps/). The app *label* stays "homework" (the default
    # from the last dotted component), so existing migrations and DB tables are unaffected.
    name = "apps.homework"
    label = "homework"
    verbose_name = "Homework"

"""Operational + settings tests: the health endpoint, the env_bool helper, self-host security
settings, an N+1 guard on the landing-page query, and the template role flags."""

import importlib
import os
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase

from apps.homework.context_processors import roles
from apps.homework.views.courses import course_cards_for
from pisa.settings import env_bool

from .utils import make_role_matrix


class HealthEndpointTests(TestCase):
    def test_healthy_when_db_reachable(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_degraded_when_db_unreachable(self):
        with mock.patch("pisa.health.connection") as conn:
            conn.cursor.side_effect = RuntimeError("db down")
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["database"], "error")


class EnvBoolTests(SimpleTestCase):
    def test_truthy_values(self):
        for raw in ("1", "true", "TRUE", "Yes", "on", "  on  "):
            with mock.patch.dict(os.environ, {"PISA_FLAG": raw}):
                self.assertTrue(env_bool("PISA_FLAG"), raw)

    def test_falsy_values(self):
        for raw in ("0", "false", "False", "no", "", "maybe"):
            with mock.patch.dict(os.environ, {"PISA_FLAG": raw}):
                self.assertFalse(env_bool("PISA_FLAG"), raw)

    def test_missing_uses_default(self):
        os.environ.pop("PISA_MISSING", None)
        self.assertFalse(env_bool("PISA_MISSING"))
        self.assertTrue(env_bool("PISA_MISSING", default=True))


class SelfHostSettingsTests(SimpleTestCase):
    """Reload the settings module under controlled env to check the deploy-time branches. The
    live ``django.conf.settings`` snapshot is unaffected; we reload back to baseline on cleanup.
    """

    def setUp(self):
        import pisa.settings as settings_module

        self.module = settings_module
        self.addCleanup(importlib.reload, settings_module)

    def _reload(self, **env):
        env.setdefault("SECRET_KEY", "test-secret")
        with mock.patch.dict(os.environ, env, clear=False):
            return importlib.reload(self.module)

    def test_production_hardening_when_debug_off(self):
        reloaded = self._reload(DEBUG="0", PISA_DOMAIN="")
        self.assertIs(reloaded.SESSION_COOKIE_SECURE, True)
        self.assertIs(reloaded.CSRF_COOKIE_SECURE, True)
        self.assertEqual(
            reloaded.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
        )

    def test_debug_relaxes_allowed_hosts(self):
        reloaded = self._reload(DEBUG="1", PISA_DOMAIN="", ALLOWED_HOSTS="")
        self.assertIn("localhost", reloaded.ALLOWED_HOSTS)

    def test_pisa_domain_drives_csrf_and_allowed_hosts(self):
        reloaded = self._reload(DEBUG="0", PISA_DOMAIN="lean.example.edu")
        self.assertEqual(reloaded.CSRF_TRUSTED_ORIGINS, ["https://lean.example.edu"])
        self.assertIn("lean.example.edu", reloaded.ALLOWED_HOSTS)


class CardsQueryCountTests(TestCase):
    def test_admin_landing_page_is_not_n_plus_1(self):
        from apps.homework.models import Course

        admin = make_role_matrix()["admin"]
        for i in range(5):
            Course.objects.create(title=f"C{i}", slug=f"c{i}")
        # Admin cards read annotated roster/draft counts, so the whole list is one query
        # regardless of how many courses exist.
        with self.assertNumQueries(1):
            course_cards_for(admin)


class RoleContextProcessorTests(TestCase):
    def setUp(self):
        self.m = make_role_matrix()
        self.factory = RequestFactory()

    def _flags(self, user):
        request = self.factory.get("/")
        request.user = user
        return roles(request)

    def test_anonymous_gets_no_flags(self):
        self.assertEqual(self._flags(AnonymousUser()), {})

    def test_admin_flags(self):
        flags = self._flags(self.m["admin"])
        self.assertTrue(flags["is_site_admin"])
        self.assertTrue(flags["is_instructor_anywhere"])

    def test_instructor_flags(self):
        flags = self._flags(self.m["instructor"])
        self.assertFalse(flags["is_site_admin"])
        self.assertTrue(flags["is_instructor_anywhere"])

    def test_ta_is_course_staff_but_not_instructor(self):
        flags = self._flags(self.m["ta"])
        self.assertFalse(flags["is_instructor_anywhere"])
        self.assertTrue(flags["is_course_staff_anywhere"])

    def test_student_flags(self):
        flags = self._flags(self.m["student"])
        self.assertTrue(flags["is_student_anywhere"])
        self.assertFalse(flags["is_course_staff_anywhere"])

    def test_outsider_has_all_flags_false(self):
        flags = self._flags(self.m["outsider"])
        self.assertFalse(any(flags.values()))

"""Health check for load balancers / uptime monitors. Unauthenticated, no DB writes."""

import logging

from django.db import connection
from django.http import JsonResponse

logger = logging.getLogger(__name__)


def healthz(request):  # noqa: ARG001 - Django's fixed view signature
    """200 if the app can reach its database, 503 otherwise."""
    # Fail closed: start unhealthy and only flip to healthy once the check actually succeeds,
    # so a future check added here that forgets to set healthy=False on its own failure path
    # can't silently leave the response healthy.
    database = "error"
    healthy = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        database = "ok"
        healthy = True
    except Exception:
        logger.exception("healthz: database check failed")
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "database": database},
        status=200 if healthy else 503,
    )

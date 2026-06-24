"""Health check for load balancers / uptime monitors. Unauthenticated, no DB writes."""

from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """200 if the app can reach its database, 503 otherwise."""
    database = "ok"
    healthy = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - any DB error means not-ready
        database = "error"
        healthy = False
    return JsonResponse(
        {"status": "ok" if healthy else "degraded", "database": database},
        status=200 if healthy else 503,
    )

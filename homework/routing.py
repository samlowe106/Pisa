from django.urls import path

from .consumers import LeanLSPConsumer

websocket_urlpatterns = [
    path("ws/lean-lsp/<int:problem_pk>/", LeanLSPConsumer.as_asgi()),
]

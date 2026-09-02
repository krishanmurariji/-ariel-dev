from django.urls import path

from recruitment.consumers import ChatConsumer

websocket_urlpatterns = [
    path("ws/chat/<int:candidate_id>/", ChatConsumer.as_asgi()),
]

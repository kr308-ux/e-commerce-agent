"""URL configuration for the Browser Agent UI."""

from django.urls import include, path


urlpatterns = [
    path("", include("tasks.urls")),
]

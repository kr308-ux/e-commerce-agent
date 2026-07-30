"""URL configuration for the Browser Agent UI."""

from django.urls import include, path


urlpatterns = [
    path("creator-contact/", include("creator_contact.urls")),
    path("mailing/", include("mailing.urls")),
    path("", include("tasks.urls")),
]

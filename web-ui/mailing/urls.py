from django.urls import path

from . import views


app_name = "mailing"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "template/preview/",
        views.template_preview,
        name="template_preview",
    ),
    path(
        "template/assets/default/",
        views.legacy_template_asset,
        name="legacy_template_asset",
    ),
    path(
        "template/assets/<int:asset_id>/",
        views.template_asset,
        name="template_asset",
    ),
    path("service/status/", views.service_status, name="service_status"),
    path("service/stop/", views.stop_service, name="stop_service"),
]

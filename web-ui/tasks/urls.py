from django.urls import path

from . import views


app_name = "tasks"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path(
        "outreach-records/",
        views.outreach_records,
        name="outreach-records",
    ),
    path("creator-data/", views.creator_data, name="creator-data"),
    path("imports/preview/", views.preview_import, name="import-preview"),
    path(
        "imports/previews/<uuid:preview_id>/instructions/",
        views.revise_import_preview,
        name="import-preview-instructions",
    ),
    path(
        "imports/previews/<uuid:preview_id>/confirm/",
        views.confirm_import,
        name="import-confirm",
    ),
    path("imports/<uuid:task_id>/", views.import_detail, name="import-detail"),
    path(
        "imports/<uuid:task_id>/status/",
        views.import_status,
        name="import-status",
    ),
    path(
        "imports/<uuid:task_id>/errors/",
        views.import_errors,
        name="import-errors",
    ),
    path(
        "imports/<uuid:task_id>/retry/",
        views.retry_import,
        name="import-retry",
    ),
]

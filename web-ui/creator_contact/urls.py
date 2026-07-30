from django.urls import path

from . import views


app_name = "creator_contact"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("candidates/", views.candidates, name="candidates"),
    path("browser-status/", views.browser_status, name="browser_status"),
    path(
        "tasks/<uuid:task_id>/",
        views.task_detail,
        name="task_detail",
    ),
    path(
        "tasks/<uuid:task_id>/status/",
        views.task_status,
        name="task_status",
    ),
    path(
        "tasks/<uuid:task_id>/start/",
        views.start_task,
        name="start_task",
    ),
    path(
        "tasks/<uuid:task_id>/cancel/",
        views.cancel_task,
        name="cancel_task",
    ),
    path(
        "collaborations/sync/",
        views.sync_collaborations,
        name="sync_collaborations",
    ),
]

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
        "tasks/<uuid:task_id>/retry/",
        views.retry_task,
        name="retry_task",
    ),
    path(
        "tasks/<uuid:task_id>/targets/<int:target_id>/confirm-review/",
        views.confirm_review_target,
        name="confirm_review_target",
    ),
    path(
        "collaborations/sync/",
        views.sync_collaborations,
        name="sync_collaborations",
    ),
]

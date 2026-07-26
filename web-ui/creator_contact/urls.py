from django.urls import path

from . import views


app_name = "creator_contact"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("candidates/", views.candidates, name="candidates"),
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
        "collaborations/sync/",
        views.sync_collaborations,
        name="sync_collaborations",
    ),
]

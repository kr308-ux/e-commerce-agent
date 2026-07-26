from django.urls import path

from . import views


app_name = "tasks"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("tasks/<uuid:task_id>/", views.task_detail, name="task-detail"),
    path("tasks/<uuid:task_id>/retry/", views.retry_task, name="task-retry"),
    path("api/tasks/<uuid:task_id>/status/", views.task_status, name="task-status"),
]

"""Presentation-only views for the initial UI phase."""

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def dashboard(request: HttpRequest) -> HttpResponse:
    """Render the static dashboard prototype."""
    return render(request, "tasks/dashboard.html")


def task_detail(request: HttpRequest) -> HttpResponse:
    """Render a static task detail prototype."""
    return render(request, "tasks/task_detail.html")

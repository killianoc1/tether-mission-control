"""HTML page views (the "Controller" for the browser UI).

These render templates only.  All data is loaded by the JavaScript front-end
from the REST API, so the pages stay light and the API stays the single source
of truth.
"""
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import Scenario


@ensure_csrf_cookie
def dashboard(request):
    """Scenario list + map; includes the create-scenario form for signed-in users."""
    return render(request, "missions/dashboard.html", {"scenario_types": Scenario.Type.choices})


@ensure_csrf_cookie
def scenario_detail(request, pk: int):
    """Mission map: layers, agent trajectories, replay slider and metrics."""
    scenario = get_object_or_404(Scenario, pk=pk)
    return render(request, "missions/scenario_detail.html", {"scenario": scenario})


def about(request):
    """Short user guide."""
    return render(request, "missions/about.html")

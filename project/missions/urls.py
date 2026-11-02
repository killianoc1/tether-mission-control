"""Browser page routes (HTML).  The data itself comes from the REST API in api/urls.py."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("scenarios/<int:pk>/", views.scenario_detail, name="scenario-detail"),
    path("guide/", views.about, name="guide"),
]

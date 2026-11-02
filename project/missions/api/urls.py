"""API routes, mounted under /api/ by config/urls.py.

A DRF ``DefaultRouter`` turns each viewset into the standard REST URLs
(list, detail, create, update, delete) and adds a browsable API index at /api/.
The extra spatial endpoints (nearest, nearby, metrics ...) are ``@action``
methods on the viewsets, so the router exposes them automatically.
"""
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from .views import RunViewSet, ScenarioViewSet

# Each register() call creates a family of URLs: /api/scenarios/, /api/scenarios/{id}/, /api/runs/ ...
router = DefaultRouter(trailing_slash=True)
router.register("scenarios", ScenarioViewSet, basename="scenario")
router.register("runs", RunViewSet, basename="run")

urlpatterns = [
    # POST username + password, get back an API token for scripts (the browser uses session login).
    path("auth/token/", obtain_auth_token, name="api-token"),
    path("", include(router.urls)),
]

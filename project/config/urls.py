"""Root URL configuration."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView


def healthz(_request):
    """Liveness probe used by the Docker health check."""
    return HttpResponse("ok", content_type="text/plain")


urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Auto-generated API documentation: the OpenAPI file and the interactive Swagger page.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/", include("missions.api.urls")),
    path("", include("missions.urls")),
]

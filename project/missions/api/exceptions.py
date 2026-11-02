"""Consistent JSON error envelope for the whole API.

Every error looks like ``{"error": {"status": 400, "message": "...", "details": {...}}}``
so every client (the browser, scripts, tests) needs only one error-handling path.
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    # Model-level validation errors (from Django, not DRF) become a normal 400 in our format.
    if isinstance(exc, DjangoValidationError):
        return Response(
            {"error": {"status": 400, "message": "Invalid data.", "details": exc.messages}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Let DRF build its standard response first (401, 403, 404, 400 ...), then re-wrap it below.
    response = exception_handler(exc, context)
    if response is None:
        if isinstance(exc, DatabaseError):
            logger.exception("Database error in API view")
            return Response(
                {"error": {"status": 500, "message": "A database error occurred.", "details": None}},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return None  # let Django produce its normal 500 page / log

    # Re-shape DRF's error body into {"error": {status, message, details}} so every error looks the same.
    detail = response.data
    message = detail.get("detail") if isinstance(detail, dict) and "detail" in detail else "Request failed."
    if response.status_code == 400:
        message = "Validation failed."
    response.data = {
        "error": {
            "status": response.status_code,
            "message": str(message),
            "details": None if isinstance(detail, dict) and set(detail) == {"detail"} else detail,
        }
    }
    return response

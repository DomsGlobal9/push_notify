"""One error shape for every API response: {"error": "...", "code": "...", "details": {...}}.

Clients (the Flutter package, any product's backend) read `code`, never the prose.
"""

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


class NotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_detail = "Not found."
    default_code = "not_found"


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API error", exc_info=exc)
        return Response(
            {"error": "Internal server error.", "code": "server_error"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    data = response.data
    code = getattr(exc, "default_code", "error")
    if hasattr(exc, "get_codes"):
        codes = exc.get_codes()
        if isinstance(codes, str):
            code = codes
    if isinstance(data, dict) and set(data) == {"detail"}:
        body = {"error": str(data["detail"]), "code": code}
    else:
        body = {"error": "Invalid request.", "code": "invalid", "details": data}
    response.data = body
    return response

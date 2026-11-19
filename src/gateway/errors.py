"""Error types used across the gateway.

The important flag is `retryable`. If it's True the provider is the one having
problems (429, 5xx, timeout...), so the router tries the next one. If it's False the
request itself is bad and we just return the error.
"""

from __future__ import annotations


class GatewayError(Exception):
    status_code: int = 500
    error_type: str = "internal_error"
    retryable: bool = False

    def __init__(
        self, message: str, *, status_code: int | None = None, upstream_status: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        # what the provider actually returned (e.g. 429), only used for logging.
        # status_code is what *we* send back to the client.
        self.upstream_status = upstream_status
        if status_code is not None:
            self.status_code = status_code


# client side problems, no fallback for these


class InvalidRequestError(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"


class AuthenticationError(GatewayError):
    status_code = 401
    error_type = "authentication_error"


class NotFoundError(GatewayError):
    status_code = 404
    error_type = "not_found"


class MethodNotAllowedError(GatewayError):
    status_code = 405
    error_type = "method_not_allowed"


class ProviderRequestError(GatewayError):
    """Provider said the request is invalid (400/404/422 etc)."""

    status_code = 400
    error_type = "invalid_request_error"


class ProviderAuthError(GatewayError):
    """Our API key for the provider is wrong (401/403). That's a config issue on our end."""

    status_code = 502
    error_type = "upstream_auth_error"


# provider side problems, these trigger fallback


class UpstreamError(GatewayError):
    status_code = 502
    error_type = "upstream_error"
    retryable = True


class UpstreamRateLimitError(UpstreamError):
    error_type = "upstream_rate_limited"


class UpstreamTimeoutError(UpstreamError):
    status_code = 504
    error_type = "upstream_timeout"


class ProviderNotConfiguredError(UpstreamError):
    """No key/url set for this provider, so we treat it like it's down."""

    status_code = 503
    error_type = "provider_unavailable"


class AllProvidersFailedError(GatewayError):
    status_code = 502
    error_type = "upstream_unavailable"

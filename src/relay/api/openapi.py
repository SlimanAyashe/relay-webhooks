"""Shared OpenAPI response-documentation snippets for the problem+json error shapes
each endpoint can actually return. Keeps every router's `responses={}` block sourcing
the same example bodies instead of re-typing RFC 9457 fields by hand each time.
"""

from relay.api.errors import PROBLEM_MEDIA_TYPE

_EXAMPLES: dict[int, dict[str, object]] = {
    401: {
        "type": "/problems/unauthorized",
        "title": "Unauthorized",
        "status": 401,
        "detail": "missing or invalid API key",
        "instance": "/v1/endpoints",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    403: {
        "type": "/problems/forbidden",
        "title": "Forbidden",
        "status": 403,
        "detail": "insufficient scope",
        "instance": "/v1/endpoints",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    404: {
        "type": "/problems/not-found",
        "title": "Not Found",
        "status": 404,
        "detail": "endpoint not found for tenant ...",
        "instance": "/v1/endpoints/2b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    409: {
        "type": "/problems/conflict",
        "title": "Conflict",
        "status": 409,
        "detail": "idempotency key reused with a differing body for tenant ...",
        "instance": "/v1/events",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    413: {
        "type": "/problems/payload-too-large",
        "title": "Payload Too Large",
        "status": 413,
        "detail": "payload too large: 131072 bytes, max 65536",
        "instance": "/v1/events",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    422: {
        "type": "/problems/validation-error",
        "title": "Validation Error",
        "status": 422,
        "detail": [
            {
                "type": "missing",
                "loc": ["body", "subscribed_event_types"],
                "msg": "Field required",
            }
        ],
        "instance": "/v1/endpoints",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
    429: {
        "type": "/problems/rate-limited",
        "title": "Too Many Requests",
        "status": 429,
        "detail": "rate limit exceeded, retry after 1.00s",
        "instance": "/v1/events",
        "trace_id": "b3f1c2a4-5e6d-4f7a-8b9c-0d1e2f3a4b5c",
    },
}

_DESCRIPTIONS: dict[int, str] = {
    401: "Missing, malformed, unknown, or revoked API key.",
    403: "A valid key that lacks the scope this endpoint requires, or a sandbox tenant "
    "that has exceeded one of its hard-capped quotas.",
    404: "No resource with this id belongs to the authenticated tenant.",
    409: "The Idempotency-Key was already used with a different request body, or the "
    "target resource isn't in a state that allows this action.",
    413: "The raw request body exceeded the configured max event payload size.",
    422: "Request body/query failed validation, or a cursor was malformed.",
    429: "The tenant's rate-limit budget is exhausted; retry after the value (seconds) "
    "in the Retry-After response header.",
}


def problem_responses(*status_codes: int) -> dict[int | str, dict[str, object]]:
    """Builds an OpenAPI `responses=` fragment for the given problem+json status codes,
    e.g. `problem_responses(401, 403, 404)`.
    """
    return {
        code: {
            "description": _DESCRIPTIONS[code],
            "content": {PROBLEM_MEDIA_TYPE: {"example": _EXAMPLES[code]}},
        }
        for code in status_codes
    }

"""HTTP helpers for the live suites: a thin client over the public API, sandbox
provisioning that respects the per-IP creation limit, and a reader for the SSE
delivery-attempt timeline.

Everything here goes over the wire. There is no in-process app, no dependency override
and no database handle -- if a test in this package passes, it passed against whatever is
actually deployed at the base URL.
"""

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

# A sandbox key is capped at 60 minutes and a handful of endpoints/events, so the suite
# provisions as few as it can and shares them across tests (see conftest).
SANDBOX_PROVISION_ATTEMPTS = 6
SANDBOX_PROVISION_BUDGET_SECONDS = 180.0

DEFAULT_ATTEMPT_TIMEOUT = 45.0


class LiveApiError(RuntimeError):
    """An unexpected response from the deployed API -- carries the body so a live failure
    is diagnosable from the pytest output alone, without shelling into the server."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(
            f"{response.request.method} {response.request.url} -> {response.status_code}: "
            f"{response.text[:500]}"
        )
        self.response = response


@dataclass(frozen=True, slots=True)
class Sandbox:
    tenant_id: str
    api_key: str
    expires_at: str
    quotas: dict[str, Any]

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


class LiveApi:
    """Every call the live suite makes, in one place, so a test body reads as the
    walkthrough an interviewer would do by hand rather than as httpx plumbing.
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    # -- raw ---------------------------------------------------------------------------

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.request(method, path, **kwargs)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self._client.post(path, **kwargs)

    # -- sandbox -----------------------------------------------------------------------

    def provision_sandbox(self) -> Sandbox:
        """POST /v1/sandbox, waiting out the per-IP creation limiter if it fires.

        The limiter (~1 sandbox per 20s, burst 3) is a real abuse control this suite also
        asserts on elsewhere; here it is simply honored -- `Retry-After` is the server's own
        answer to "when may I ask again", so waiting exactly that long both respects the
        control and proves the header is usable.
        """
        deadline = time.monotonic() + SANDBOX_PROVISION_BUDGET_SECONDS
        last: httpx.Response | None = None
        for _ in range(SANDBOX_PROVISION_ATTEMPTS):
            response = self._client.post("/v1/sandbox")
            if response.status_code == 201:
                body = response.json()
                return Sandbox(
                    tenant_id=body["tenant_id"],
                    api_key=body["api_key"],
                    expires_at=body["expires_at"],
                    quotas=body["quotas"],
                )
            last = response
            if response.status_code != 429:
                raise LiveApiError(response)
            wait = float(response.headers.get("Retry-After", "20")) + 1.0
            if time.monotonic() + wait > deadline:
                break
            time.sleep(wait)
        assert last is not None
        raise LiveApiError(last)

    def sandbox_metrics(self, sandbox: Sandbox) -> dict[str, Any]:
        response = self._client.get("/v1/sandbox/metrics", headers=sandbox.headers)
        if response.status_code != 200:
            raise LiveApiError(response)
        return dict(response.json())

    def verify_signature(
        self,
        sandbox: Sandbox,
        *,
        secret: str,
        timestamp: int,
        body: str,
        signature: str,
        tolerance_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Returns the whole verdict, not just `valid`. Asserting on `reason` is what
        makes a negative test meaningful: `not valid` alone would also be satisfied by a
        delivery that merely aged out of the replay window mid-test, which would let a
        broken signature check pass as a green tamper-detection test.
        """
        payload: dict[str, Any] = {
            "secret": secret,
            "timestamp": timestamp,
            "body": body,
            "signature": signature,
        }
        if tolerance_seconds is not None:
            payload["tolerance_seconds"] = tolerance_seconds
        response = self._client.post(
            "/v1/sandbox/verify-signature",
            headers=sandbox.headers,
            json=payload,
        )
        if response.status_code != 200:
            raise LiveApiError(response)
        return dict(response.json())

    # -- endpoints ---------------------------------------------------------------------

    def register_endpoint(
        self, sandbox: Sandbox, url: str, event_types: list[str]
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/endpoints",
            headers=sandbox.headers,
            json={"url": url, "subscribed_event_types": event_types},
        )
        if response.status_code != 201:
            raise LiveApiError(response)
        return dict(response.json())

    def repoint_endpoint(self, sandbox: Sandbox, endpoint_id: str, url: str) -> dict[str, Any]:
        """PATCHes an existing endpoint's URL instead of registering another one -- a
        sandbox is capped at a small fixed endpoint count (that cap is itself under test
        elsewhere), so probes that need many destinations reuse one endpoint.
        """
        response = self._client.patch(
            f"/v1/endpoints/{endpoint_id}", headers=sandbox.headers, json={"url": url}
        )
        if response.status_code != 200:
            raise LiveApiError(response)
        return dict(response.json())

    # -- events ------------------------------------------------------------------------

    def trigger(
        self,
        sandbox: Sandbox,
        *,
        event_type: str = "demo.triggered",
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        headers = {
            **sandbox.headers,
            "Idempotency-Key": idempotency_key or str(uuid.uuid4()),
            **(extra_headers or {}),
        }
        return self._client.post(
            "/v1/events",
            headers=headers,
            json={"type": event_type, "payload": payload or {"probe": str(uuid.uuid4())}},
        )

    def trigger_settled(self, sandbox: Sandbox, **kwargs: Any) -> httpx.Response:
        """Triggers an event and returns the first response that isn't a rate-limit
        deferral -- for tests asserting on some *other* rejection (409, 403, 413), which the
        limiter would otherwise answer first."""
        for _ in range(8):
            response = self.trigger(sandbox, **kwargs)
            if response.status_code != 429:
                return response
            time.sleep(float(response.headers.get("Retry-After", "1")) + 0.2)
        return response

    def trigger_accepted(self, sandbox: Sandbox, **kwargs: Any) -> httpx.Response:
        """Triggers an event, obeying the tenant's per-second budget on the way.

        A sandbox's rate limit is deliberately tight (1/s, burst 3), and a test suite is a
        burst. Waiting out `Retry-After` -- rather than raising -- keeps the limiter under
        test where it belongs (`test_abuse_controls_live.py`) instead of turning every other
        test into a coin flip.
        """
        for _ in range(8):
            response = self.trigger(sandbox, **kwargs)
            if response.status_code == 202:
                return response
            if response.status_code != 429:
                raise LiveApiError(response)
            time.sleep(float(response.headers.get("Retry-After", "1")) + 0.2)
        raise LiveApiError(response)

    # -- dlq ---------------------------------------------------------------------------

    def dlq(self, sandbox: Sandbox) -> list[dict[str, Any]]:
        response = self._client.get("/v1/dlq", headers=sandbox.headers)
        if response.status_code != 200:
            raise LiveApiError(response)
        return list(response.json()["items"])

    def replay(self, sandbox: Sandbox, delivery_id: str) -> httpx.Response:
        return self._client.post(f"/v1/deliveries/{delivery_id}/replay", headers=sandbox.headers)


class AttemptFeed:
    """Reads `GET /v1/sandbox/stream` (Server-Sent Events) in a background thread and
    makes the tenant's delivery attempts awaitable from a test.

    This is the same live attempt log the demo console renders, consumed the same way --
    which is the point: the smoke suite observes outcomes through a surface a user has,
    not by querying the database behind the API's back.
    """

    def __init__(self, base_url: str, sandbox: Sandbox) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/sandbox/stream?api_key={sandbox.api_key}"
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.errors: list[str] = []

    def start(self) -> "AttemptFeed":
        self._thread = threading.Thread(target=self._run, name="attempt-feed", daemon=True)
        self._thread.start()
        # The stream sends nothing until an attempt happens, so "ready" means the response
        # headers came back -- enough to know a subsequent trigger can't be missed.
        self._ready.wait(timeout=15.0)
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with (
                    httpx.Client(timeout=httpx.Timeout(10.0, read=None)) as client,
                    client.stream("GET", self._url) as response,
                ):
                    response.raise_for_status()
                    self._ready.set()
                    for line in response.iter_lines():
                        if self._stop.is_set():
                            return
                        if not line.startswith("data: "):
                            continue  # `: keep-alive` comments and blank separators
                        with self._lock:
                            self._events.append(json.loads(line[len("data: ") :]))
            except Exception as exc:  # pragma: no cover - diagnostic path
                self.errors.append(repr(exc))
                self._ready.set()
                if self._stop.wait(1.0):
                    return

    @property
    def events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = DEFAULT_ATTEMPT_TIMEOUT,
        description: str = "a matching delivery attempt",
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events:
                if predicate(event):
                    return event
            time.sleep(0.25)
        raise AssertionError(
            f"timed out after {timeout}s waiting for {description}. "
            f"Attempts seen: {json.dumps(self.events, indent=2)[:4000]}. "
            f"Stream errors: {self.errors}"
        )

    def wait_for_delivery_state(
        self, delivery_predicate: Callable[[dict[str, Any]], bool], state: str, **kwargs: Any
    ) -> dict[str, Any]:
        return self.wait_for(
            lambda e: delivery_predicate(e) and e["delivery_state"] == state,
            description=f"a delivery reaching state {state!r}",
            **kwargs,
        )


@contextmanager
def attempt_feed(base_url: str, sandbox: Sandbox) -> Iterator[AttemptFeed]:
    feed = AttemptFeed(base_url, sandbox).start()
    try:
        yield feed
    finally:
        feed.stop()


@dataclass
class EndpointHandle:
    """An endpoint plus the secret returned once at creation -- the pair a receiver needs
    to verify signatures, kept together so tests don't juggle two variables."""

    id: str
    url: str
    secret: str
    events: list[str] = field(default_factory=list)

    @classmethod
    def from_response(cls, body: dict[str, Any]) -> "EndpointHandle":
        return cls(
            id=body["id"],
            url=body["url"],
            secret=body["secret"],
            events=list(body["subscribed_event_types"]),
        )

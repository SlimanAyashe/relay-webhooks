"""In-memory fakes for infra ports, used where a full respx-mocked httpx call would be
more machinery than a test needs. See relay.infra.http_sender.OutboundHttpSender.
"""

from collections import deque
from collections.abc import Iterable

from relay.infra.http_sender import OutboundHttpResult


class FakeOutboundHttpSender:
    """Returns a scripted sequence of results, one per call, so a test can assert exactly
    what DeliveryAttemptService does with a given outcome without any real network I/O.
    """

    def __init__(self, results: Iterable[OutboundHttpResult]) -> None:
        self._results: deque[OutboundHttpResult] = deque(results)
        self.calls: list[dict[str, object]] = []

    async def send(
        self, *, url: str, payload: bytes, headers: dict[str, str]
    ) -> OutboundHttpResult:
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        if not self._results:
            raise AssertionError("FakeOutboundHttpSender.send() called more times than scripted")
        return self._results.popleft()

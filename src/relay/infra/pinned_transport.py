"""The IP-pinned httpx transport: closes the DNS-rebinding TOCTOU window between
`ssrf_guard.check_url()` validating a hostname and the HTTP client actually connecting.

Validating a hostname and then handing that *hostname* back to an HTTP client is a bug --
the client resolves again, and a second lookup (an attacker-controlled or just
short-TTL-and-unlucky record) can return a different address than the one that was
validated. This transport instead rewrites the outgoing request to connect directly to the
already-validated IP, while preserving the original `Host` header and setting
`sni_hostname` so TLS SNI and certificate verification still target the real hostname --
otherwise a certificate for `example.com` would fail to validate against a raw IP address.
"""

import httpx


class PinnedAsyncTransport(httpx.AsyncHTTPTransport):
    """An `httpx.AsyncHTTPTransport` that connects to `pinned_ip` no matter what host the
    request's URL names, while keeping the `Host` header and TLS SNI/cert verification
    pointed at `original_host` -- so from the destination's point of view (and from TLS's
    point of view) nothing looks different, only the actual socket connects elsewhere.
    """

    def __init__(self, *, pinned_ip: str, original_host: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._pinned_ip = pinned_ip
        self._original_host = original_host

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_authority = request.headers.get("host") or request.url.netloc.decode("ascii")
        request.url = request.url.copy_with(host=self._pinned_ip)
        request.headers["host"] = original_authority
        request.extensions = {**request.extensions, "sni_hostname": self._original_host}
        return await super().handle_async_request(request)

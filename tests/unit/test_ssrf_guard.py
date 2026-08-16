from relay.infra.ssrf_guard import Resolver, check_url

_ALLOWED_PORTS = frozenset({80, 443})


def _resolver_returning(*ips: str) -> Resolver:
    def _resolve(hostname: str) -> list[str]:
        return list(ips)

    return _resolve


def test_allows_a_public_address() -> None:
    result = check_url(
        "https://example.com/webhook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("93.184.216.34"),
    )

    assert result.allowed is True
    assert result.resolved_ip == "93.184.216.34"
    assert result.hostname == "example.com"
    assert result.port == 443


def test_denies_loopback() -> None:
    result = check_url(
        "https://localhost/webhook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("127.0.0.1"),
    )
    assert result.allowed is False


def test_denies_rfc1918_private_range() -> None:
    for private_ip in ("10.0.0.1", "172.16.0.1", "192.168.1.1"):
        result = check_url(
            "https://internal.example.com/hook",
            allowed_ports=_ALLOWED_PORTS,
            resolver=_resolver_returning(private_ip),
        )
        assert result.allowed is False, private_ip


def test_denies_link_local() -> None:
    result = check_url(
        "https://x.example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("169.254.1.1"),
    )
    assert result.allowed is False


def test_denies_cloud_metadata_ip_explicitly() -> None:
    """169.254.169.254 -- the AWS/GCP/Azure instance metadata endpoint. The single most
    important thing this guard exists to block.
    """
    result = check_url(
        "https://metadata.example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("169.254.169.254"),
    )
    assert result.allowed is False
    assert result.resolved_ip == "169.254.169.254"


def test_denies_cgnat_range() -> None:
    """100.64.0.0/10 -- RFC 6598 shared address space. Not covered by ipaddress's own
    is_private, so the guard needs its own explicit check for it.
    """
    result = check_url(
        "https://cgnat.example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("100.64.0.1"),
    )
    assert result.allowed is False


def test_denies_ipv6_unique_local_address() -> None:
    result = check_url(
        "https://v6.example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("fc00::1"),
    )
    assert result.allowed is False


def test_denies_when_any_resolved_address_is_forbidden() -> None:
    """A hostname resolving to multiple addresses is denied if *any* of them is forbidden,
    not only if the first one is -- otherwise a benign-first, forbidden-second DNS answer
    would slip through a guard that only checked candidates[0].
    """
    result = check_url(
        "https://multi.example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("93.184.216.34", "127.0.0.1"),
    )
    assert result.allowed is False


def test_denies_disallowed_port() -> None:
    result = check_url(
        "https://example.com:8080/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("93.184.216.34"),
    )
    assert result.allowed is False
    assert result.port == 8080


def test_allows_configured_extra_port() -> None:
    result = check_url(
        "https://example.com:8443/hook",
        allowed_ports=frozenset({80, 443, 8443}),
        resolver=_resolver_returning("93.184.216.34"),
    )
    assert result.allowed is True


def test_denies_non_http_scheme() -> None:
    result = check_url(
        "ftp://example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("93.184.216.34"),
    )
    assert result.allowed is False


def test_denies_dns_resolution_failure() -> None:
    def _raise(hostname: str) -> list[str]:
        raise OSError("nxdomain")

    result = check_url(
        "https://nonexistent.invalid/hook", allowed_ports=_ALLOWED_PORTS, resolver=_raise
    )
    assert result.allowed is False


def test_default_port_for_http_is_80() -> None:
    result = check_url(
        "http://example.com/hook",
        allowed_ports=_ALLOWED_PORTS,
        resolver=_resolver_returning("93.184.216.34"),
    )
    assert result.allowed is True
    assert result.port == 80

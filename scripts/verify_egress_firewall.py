#!/usr/bin/env python3
"""Check, from inside a Relay worker container, whether the *network layer* refuses
connections to private/link-local addresses -- with the application's SSRF guard entirely
out of the picture.

`relay.infra.ssrf_guard` is the primary defense and is covered by tests. This script tests
the second, independent layer `docs/guarantees.md` and `docs/runbook.md` describe: a host
firewall rule that would still hold if the guard had a bug, was refactored around, or was
bypassed by some future code path that makes an outbound call another way. Until this has
actually been run on the real VPS, "defense in depth" is a claim about a code comment.

It opens raw TCP sockets from the dispatcher container -- no HTTP, no Relay code -- and
reports, per target, what the kernel did. Run it on the host that runs the stack:

    python3 scripts/verify_egress_firewall.py            # human-readable report
    python3 scripts/verify_egress_firewall.py --markdown # paste-ready for docs/runbook.md

Exit status:
    0  the network layer blocks every decisive private target (defense in depth is real)
    1  a decisive private target was reachable -- the app-layer guard is the only defense
    2  the probe itself is inconclusive (public control or intra-network check failed)

A note on reading the results: a *timeout* against an address with nothing listening is
ambiguous -- it looks the same whether a firewall dropped the packet or the address simply
does not answer. That is why the decisive targets are ones that demonstrably do listen
(the host's own SSH port, reachable over the container bridge). Those turn a DROP rule into
an observable difference instead of an assumption.
"""

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass

WORKER_SERVICE = "dispatcher"
COMPOSE_PROJECT = "relay"
CONNECT_TIMEOUT_SECONDS = 3

MUST_BE_BLOCKED = "must-be-blocked"
INFORMATIONAL = "informational"
MUST_CONNECT = "must-connect"


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int
    expectation: str
    why: str


@dataclass(frozen=True)
class Probe:
    target: Target
    outcome: str
    detail: str

    @property
    def connected(self) -> bool:
        return self.outcome == "CONNECTED"

    @property
    def ok(self) -> bool:
        if self.target.expectation == MUST_BE_BLOCKED:
            return not self.connected
        if self.target.expectation == MUST_CONNECT:
            return self.connected
        return True


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout.strip()


def container_name(service: str) -> str:
    return f"{COMPOSE_PROJECT}-{service}-1"


def bridge_gateways() -> dict[str, str]:
    """Every Docker bridge's gateway address on this host -- each one is the host itself,
    reachable from a container on that bridge unless something stops it."""
    names = _run(["docker", "network", "ls", "--filter", "driver=bridge", "--format", "{{.Name}}"])
    gateways: dict[str, str] = {}
    for network in names.splitlines():
        raw = _run(["docker", "network", "inspect", network, "--format", "{{json .IPAM.Config}}"])
        try:
            for config in json.loads(raw or "[]"):
                if config.get("Gateway"):
                    gateways[network] = config["Gateway"]
        except json.JSONDecodeError:
            continue
    return gateways


def build_targets() -> list[Target]:
    gateways = bridge_gateways()
    own_gateway = gateways.get(f"{COMPOSE_PROJECT}_default")
    other_gateways = {
        network: address
        for network, address in gateways.items()
        if network != f"{COMPOSE_PROJECT}_default"
    }

    targets: list[Target] = []
    if own_gateway:
        targets.append(
            Target(
                "host over the container bridge (sshd)",
                own_gateway,
                22,
                MUST_BE_BLOCKED,
                "the host's own private interface -- the one private address a container "
                "on this bridge can definitely reach, so the decisive probe",
            )
        )
    for network, address in sorted(other_gateways.items())[:2]:
        targets.append(
            Target(
                f"host over {network}'s bridge (sshd)",
                address,
                22,
                MUST_BE_BLOCKED,
                "another service's bridge gateway -- same host, different private address",
            )
        )
    targets += [
        Target(
            "cloud metadata service",
            "169.254.169.254",
            80,
            INFORMATIONAL,
            "nothing listens here on this VPS, so a timeout proves nothing on its own -- "
            "recorded because it is the address the guard exists for",
        ),
        Target("RFC1918 10/8", "10.0.0.1", 80, INFORMATIONAL, "no listener; ambiguous by nature"),
        Target(
            "RFC1918 192.168/16",
            "192.168.0.1",
            80,
            INFORMATIONAL,
            "no listener; ambiguous by nature",
        ),
        Target(
            "public internet (control)",
            "1.1.1.1",
            443,
            MUST_CONNECT,
            "proves the probe works at all and that outbound delivery still functions",
        ),
        Target(
            "own database over the container network (control)",
            "postgres",
            5432,
            MUST_CONNECT,
            "intra-network traffic the stack needs -- any rule set that breaks this is wrong",
        ),
    ]
    return targets


PROBE_SOURCE = """
import json, socket, sys
results = []
for host, port in json.loads(sys.argv[1]):
    sock = socket.socket()
    sock.settimeout({timeout})
    try:
        sock.connect((host, int(port)))
        results.append([host, port, "CONNECTED", ""])
    except Exception as exc:
        results.append([host, port, type(exc).__name__, str(exc)])
    finally:
        sock.close()
print(json.dumps(results))
"""


def probe(targets: list[Target], *, container: str) -> list[Probe]:
    payload = json.dumps([[t.host, t.port] for t in targets])
    raw = _run(
        [
            "docker",
            "exec",
            container,
            "python",
            "-c",
            PROBE_SOURCE.format(timeout=CONNECT_TIMEOUT_SECONDS),
            payload,
        ]
    )
    if not raw:
        print(f"could not run the probe inside {container}", file=sys.stderr)
        raise SystemExit(2)
    outcomes = json.loads(raw)
    return [
        Probe(target=target, outcome=outcome, detail=detail)
        for target, (_host, _port, outcome, detail) in zip(targets, outcomes, strict=True)
    ]


def rule_sets() -> dict[str, str]:
    """The host's current filtering rules, recorded alongside the result -- a pass means
    nothing without the rules that produced it."""
    captured: dict[str, str] = {}
    if shutil.which("nft"):
        captured["nft list ruleset"] = _run(["nft", "list", "ruleset"]) or "(empty)"
    if shutil.which("iptables"):
        captured["iptables -S DOCKER-USER"] = _run(["iptables", "-S", "DOCKER-USER"]) or "(empty)"
        captured["iptables -S INPUT"] = _run(["iptables", "-S", "INPUT"]) or "(empty)"
    if shutil.which("ufw"):
        captured["ufw status"] = _run(["ufw", "status"]) or "(empty)"
    return captured


def report(probes: list[Probe], *, markdown: bool) -> None:
    if markdown:
        print("| Target | Address | Expectation | Result |")
        print("| --- | --- | --- | --- |")
        for p in probes:
            print(
                f"| {p.target.name} | `{p.target.host}:{p.target.port}` | "
                f"{p.target.expectation} | `{p.outcome}` |"
            )
        return
    width = max(len(p.target.name) for p in probes)
    for p in probes:
        verdict = "ok" if p.ok else "PROBLEM"
        print(
            f"  [{verdict:>7}] {p.target.name:<{width}}  {p.target.host}:{p.target.port:<5} "
            f"-> {p.outcome} {p.detail}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--markdown", action="store_true", help="emit a table for the runbook")
    parser.add_argument("--service", default=WORKER_SERVICE, help="compose service to probe from")
    args = parser.parse_args(argv)

    container = container_name(args.service)
    targets = build_targets()
    probes = probe(targets, container=container)

    if not args.markdown:
        print(f"egress probe from {container}:")
    report(probes, markdown=args.markdown)

    reachable = [p for p in probes if p.target.expectation == MUST_BE_BLOCKED and p.connected]
    controls_failed = [p for p in probes if p.target.expectation == MUST_CONNECT and not p.ok]

    print()
    for name, rules in rule_sets().items():
        print(f"--- {name} ---")
        print(rules)
        print()

    if controls_failed:
        print(
            "INCONCLUSIVE: a control target failed, so nothing here can be trusted: "
            f"{[p.target.name for p in controls_failed]}"
        )
        return 2
    if reachable:
        print(
            "NOT BLOCKED at the network layer: "
            f"{', '.join(f'{p.target.host}:{p.target.port}' for p in reachable)} answered a "
            "connection from inside the container. The application's SSRF guard is currently "
            "the only thing preventing Relay from reaching these addresses -- see "
            "docs/runbook.md 'Defense in depth: VPS egress firewall'."
        )
        return 1
    print("BLOCKED at the network layer: every decisive private target refused the connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

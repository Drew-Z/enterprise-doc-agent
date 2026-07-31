from __future__ import annotations

import ipaddress
import sys


def validate_operator_cidr(value: str) -> None:
    network = ipaddress.ip_network(value, strict=False)
    if network.prefixlen != network.max_prefixlen:
        raise ValueError("operator SSH CIDR must identify exactly one host (/32 or /128)")
    if network.is_unspecified or network.is_multicast:
        raise ValueError("operator SSH CIDR must be a unicast host")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_operator_cidr.py <IPv4-or-IPv6-host-CIDR>", file=sys.stderr)
        return 2
    try:
        validate_operator_cidr(sys.argv[1])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

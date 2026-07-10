"""Remote-deployment security policy for the prototype web application."""

from __future__ import annotations

import os

MIN_AUTH_TOKEN_LENGTH = 32
LOCAL_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "testserver", "[::1]")


def remote_allowed_hosts(explicit: list[str] | None = None) -> list[str]:
    values = explicit
    if values is None:
        values = os.environ.get("FINWATCH_ALLOWED_HOSTS", "").split(",")
    hosts = [value.strip().lower() for value in values if value.strip()]
    if not hosts:
        raise RuntimeError(
            "Remote serving requires FINWATCH_ALLOWED_HOSTS (comma-separated hostnames)."
        )
    if any(host == "*" or "://" in host or "/" in host for host in hosts):
        raise RuntimeError(
            "FINWATCH_ALLOWED_HOSTS must contain explicit hostnames, never '*' or URLs."
        )
    return hosts


def remote_auth_token(explicit: str | None = None) -> str:
    token = explicit if explicit is not None else os.environ.get("FINWATCH_AUTH_TOKEN", "")
    token = token.strip()
    if len(token) < MIN_AUTH_TOKEN_LENGTH:
        raise RuntimeError(
            f"Remote serving requires FINWATCH_AUTH_TOKEN with at least "
            f"{MIN_AUTH_TOKEN_LENGTH} characters."
        )
    return token

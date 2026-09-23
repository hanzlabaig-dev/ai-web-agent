"""
security.py — Security boundary (TESTING MODE — enforcement DISABLED).

⚠️  WARNING: This file has been modified for local testing against a
    single known website.. The following enforcement
    checks have been DISABLED:

      - Single-domain lock (any hostname is now reachable)
      - Private/loopback/link-local/metadata IP block
      - Path-smuggling check (//host, http://host in path)
      - Redirect target validation

    What is STILL active (because it's hygiene, not enforcement):
      - Sensitive header / JSON-key redaction (keeps logs clean)
      - POST body size limit (prevents accidental DoS)
      - Basic URL sanity checks (http/https only, non-empty)

    DO NOT use this configuration against any site you do not own.
    DO NOT leave this file in this state permanently — restore from
    backup when testing is finished.
"""

import ipaddress
import socket
from urllib.parse import urlparse

import config


class SecurityError(Exception):
    """Raised whenever a request fails basic URL sanity checks."""


def _get_target_hostname() -> str:
    """
    Returns the hostname from TARGET_BASE_URL, lowercased.

    NOTE: In testing mode this is only used for display / reference.
    It is NOT enforced anywhere — requests to other hosts are allowed.
    """
    if not config.TARGET_BASE_URL:
        raise SecurityError("TARGET_BASE_URL is not configured.")
    parsed = urlparse(config.TARGET_BASE_URL)
    if not parsed.hostname:
        raise SecurityError("TARGET_BASE_URL has no hostname.")
    return parsed.hostname.lower()


def _is_private_or_dangerous_ip(hostname: str) -> bool:
    """
    DISABLED IN TESTING MODE.

    Original behavior: resolve hostname and return True if it points to
    a private, loopback, link-local, reserved, multicast, or unspecified
    IP address (including cloud metadata endpoints like 169.254.169.254).

    Current behavior: always returns False — nothing is blocked.

    Kept in the file (unused) so the code can be restored by simply
    re-enabling the call in validate_url below.
    """
    # --- ORIGINAL IMPLEMENTATION (kept for easy restore) ----------------
    # dangerous_literal_hosts = {
    #     "localhost",
    #     "metadata.google.internal",
    #     "169.254.169.254",
    # }
    # if hostname in dangerous_literal_hosts:
    #     return True
    #
    # try:
    #     infos = socket.getaddrinfo(hostname, None)
    # except socket.gaierror:
    #     return True
    #
    # for info in infos:
    #     ip_str = info[4][0]
    #     try:
    #         ip = ipaddress.ip_address(ip_str)
    #     except ValueError:
    #         return True
    #     if (
    #         ip.is_private
    #         or ip.is_loopback
    #         or ip.is_link_local
    #         or ip.is_reserved
    #         or ip.is_multicast
    #         or ip.is_unspecified
    #     ):
    #         return True
    # return False
    # ---------------------------------------------------------------------
    return False


def validate_url(url: str, *, context: str = "request") -> str:
    """
    TESTING MODE — minimal validation only.

    Still enforced:
      - URL must be a non-empty string
      - Scheme must be http or https (requests library requirement)

    NO LONGER enforced (disabled in testing mode):
      - file:// / javascript: / data: scheme block
      - Single-domain lock
      - Private/loopback/metadata IP block

    Returns the URL unchanged if it passes basic sanity checks, otherwise
    raises SecurityError.
    """
    if not isinstance(url, str) or not url.strip():
        raise SecurityError(f"Empty or invalid URL in {context}.")

    url = url.strip()

    # --- DISABLED: dangerous-scheme block -------------------------------
    # lowered = url.lower()
    # if lowered.startswith("javascript:") or lowered.startswith("file:") or lowered.startswith("data:"):
    #     raise SecurityError(f"Disallowed URL scheme in {context}: {url}")
    # ---------------------------------------------------------------------

    parsed = urlparse(url)

    # Keep a minimal scheme check — `requests` only supports http/https
    # anyway. If you also want to try file:// (it will not work with
    # requests), remove or extend this check.
    if parsed.scheme not in ("http", "https"):
        raise SecurityError(f"Disallowed scheme '{parsed.scheme}' in {context}.")

    if not parsed.hostname:
        raise SecurityError(f"URL has no hostname in {context}: {url}")

    # --- DISABLED: single-domain lock -----------------------------------
    # target_host = _get_target_hostname()
    # host = parsed.hostname.lower()
    # if host != target_host:
    #     raise SecurityError(
    #         f"Blocked request to '{host}' — only '{target_host}' is permitted."
    #     )
    # ---------------------------------------------------------------------

    # --- DISABLED: private / loopback / metadata IP block ---------------
    # host = parsed.hostname.lower()
    # if _is_private_or_dangerous_ip(host):
    #     raise SecurityError(
    #         f"Blocked request to '{host}' — resolves to a private, loopback, "
    #         f"reserved, or metadata address."
    #     )
    # ---------------------------------------------------------------------

    return url


def build_target_url(path: str) -> str:
    """
    TESTING MODE — joins a path onto TARGET_BASE_URL.

    NO LONGER rejects scheme-relative or absolute-URL smuggling in the
    path. A path of "http://evil.com" or "//evil.com" will be concatenated
    onto the base URL as-is and passed through to validate_url (which, in
    this mode, will not block it either).
    """
    if not isinstance(path, str):
        raise SecurityError("Path must be a string.")

    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path

    # --- DISABLED: path-smuggling check ---------------------------------
    # if path.startswith("//") or "://" in path:
    #     raise SecurityError(f"Path attempts to redirect to another host: {path}")
    # ---------------------------------------------------------------------

    base = config.TARGET_BASE_URL.rstrip("/")
    full_url = base + path
    return validate_url(full_url, context="constructed path")


def validate_redirect(current_url: str, location_header: str) -> str:
    """
    TESTING MODE — resolves a redirect Location header against the current
    URL but performs NO validation on the result.

    In normal mode this would re-apply the domain lock and IP block to the
    redirect target. In testing mode, the redirect target is returned as-is
    so the caller can follow it freely.
    """
    from urllib.parse import urljoin

    if not location_header:
        raise SecurityError("Redirect with empty Location header.")

    resolved = urljoin(current_url, location_header)

    # --- DISABLED: redirect validation ----------------------------------
    # return validate_url(resolved, context="redirect target")
    # ---------------------------------------------------------------------

    return resolved


# ---------------------------------------------------------------------------
# Redaction helpers — KEPT ACTIVE.
# These are not enforcement; they keep secrets out of log files and out of
# anything printed to the terminal. Safe to leave on.
# ---------------------------------------------------------------------------

def redact_headers(headers: dict) -> dict:
    """Return a copy of headers with sensitive values redacted."""
    redacted = {}
    for key, value in headers.items():
        if key.lower() in config.SENSITIVE_HEADER_NAMES:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted


def redact_json_body(data):
    """Recursively redact sensitive keys in a JSON-like structure for logging."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if isinstance(k, str) and k.lower() in config.SENSITIVE_JSON_KEYS:
                result[k] = "***REDACTED***"
            else:
                result[k] = redact_json_body(v)
        return result
    if isinstance(data, list):
        return [redact_json_body(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# Body-size guard — KEPT ACTIVE.
# Prevents an accidental 500MB POST. Not enforcement; a basic sanity limit.
# ---------------------------------------------------------------------------

def enforce_post_body_size(raw_body: bytes) -> None:
    if len(raw_body) > config.MAX_POST_BODY_BYTES:
        raise SecurityError(
            f"POST body of {len(raw_body)} bytes exceeds limit of "
            f"{config.MAX_POST_BODY_BYTES} bytes."
        )
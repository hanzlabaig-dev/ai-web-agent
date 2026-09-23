"""
tools.py — The actual capabilities exposed to the LLM.

TESTING MODE: Redirect auto-follow is ENABLED, and the POST confirmation
prompt has been DISABLED. All other behavior is unchanged. Every tool
still calls security.build_target_url() and security.enforce_post_body_size(),
but those checks are relaxed in security.py's testing mode.

⚠️  DO NOT use this configuration against any site you do not own.
"""

import json
import re
import time
from urllib.parse import urljoin, urlparse

import requests

import config
import logger
import security

_last_request_time = 0.0

_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

# A persistent session so cookies (e.g. from a login on your own site) carry
# across tool calls within a run — lets the agent test logged-in flows on
# YOUR site using its own session, not anyone else's. Reset with
# tool_reset_session() / the reset_session tool.
_session = requests.Session()


def _enforce_rate_limit():
    if not config.ENABLE_RATE_LIMIT:
        return
    global _last_request_time
    elapsed = time.time() - _last_request_time
    wait = config.MIN_SECONDS_BETWEEN_REQUESTS - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_time = time.time()


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + f"\n...[TRUNCATED at {max_bytes} bytes]"


# Headers the agent may never set itself — Host and Content-Length can be
# abused for request smuggling / SSRF on misconfigured proxies. Kept even
# in testing mode because there is no legitimate use case for the LLM to
# set these.
_DISALLOWED_CUSTOM_HEADERS = {"host", "content-length"}


def _merge_headers(custom_headers: dict = None) -> dict:
    headers = {"User-Agent": "controlled-web-agent/1.0"}
    if custom_headers:
        for k, v in custom_headers.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            if k.lower() in _DISALLOWED_CUSTOM_HEADERS:
                continue
            headers[k] = v
    return headers


def _safe_request(method: str, url: str, *, json_body=None, form_body=None, custom_headers: dict = None):
    """
    TESTING MODE: redirects are followed automatically by the requests
    library (allow_redirects=True). The old manual per-hop validation loop
    has been removed.

    Pass either json_body (sent as JSON) or form_body (sent as
    application/x-www-form-urlencoded), never both.

    A redirect that leaves the original domain will still be followed in
    this mode — there is no longer any check on the redirect target.
    """
    headers = _merge_headers(custom_headers)

    try:
        response = _session.request(
            method,
            url,
            json=json_body,
            data=form_body,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            allow_redirects=True,   # TESTING MODE: auto-follow all redirects
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Request to {url} timed out after {config.REQUEST_TIMEOUT_SECONDS}s.")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Connection error contacting {url}: {e}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Request to {url} failed: {e}")

    # No redirect_info to return in testing mode — the requests library has
    # already followed any redirects. Callers receive (response, None).
    return response, None


def tool_get_page(path: str, headers: dict = None) -> dict:
    """
    GET tool: fetch a page/endpoint on the configured target domain.
    Returns a dict safe to hand back to the LLM (no secrets).
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("get_page", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("GET", url, custom_headers=headers)
    except RuntimeError as e:
        logger.log_tool_call("get_page", path=path, url=url, method="GET", error=str(e))
        return {"error": str(e)}

    body = _truncate(response.text, config.MAX_RESPONSE_BYTES)
    content_type = response.headers.get("Content-Type", "unknown")

    debug_headers = {
        k: v
        for k, v in response.headers.items()
        if k.lower() in ("content-type", "content-length", "server", "cache-control", "location")
    }

    logger.log_tool_call(
        "get_page",
        path=path,
        url=url,
        method="GET",
        status=response.status_code,
        response_headers=dict(response.headers),
    )

    result = {
        "url": url,
        "final_url": response.url,  # TESTING: shows where redirects landed
        "status": response.status_code,
        "content_type": content_type,
        "headers": security.redact_headers(debug_headers),
        "body": body,
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def tool_batch_get(paths: list) -> dict:
    """
    Batch GET tool: fetch several paths on the target domain in one tool
    call. Each path still passes through the same per-request security
    validation and rate limiting as a single get_page call — this is just
    a convenience wrapper to cut down on round trips with the LLM.
    """
    if not isinstance(paths, list) or not paths:
        return {"error": "paths must be a non-empty list of strings."}

    max_batch = max(1, config.MAX_TOOL_CALLS_PER_TASK)
    if len(paths) > max_batch:
        return {"error": f"Batch of {len(paths)} exceeds MAX_TOOL_CALLS_PER_TASK ({max_batch})."}

    results = {}
    for p in paths:
        if not isinstance(p, str):
            results[str(p)] = {"error": "Path must be a string."}
            continue
        results[p] = tool_get_page(p)
    return {"results": results}


def tool_head_page(path: str) -> dict:
    """
    HEAD tool: fetch only headers/status for a path, no body. Fast way to
    check whether an endpoint exists, its content type, size, or caching
    behavior without downloading the full response.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("head_page", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("HEAD", url)
    except RuntimeError as e:
        logger.log_tool_call("head_page", path=path, url=url, method="HEAD", error=str(e))
        return {"error": str(e)}

    logger.log_tool_call(
        "head_page", path=path, url=url, method="HEAD",
        status=response.status_code, response_headers=dict(response.headers),
    )

    result = {
        "url": url,
        "final_url": response.url,  # TESTING: shows where redirects landed
        "status": response.status_code,
        "headers": security.redact_headers(dict(response.headers)),
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def tool_crawl_links(path: str, max_links: int = 30) -> dict:
    """
    Fetch a page and extract same-domain links found in its HTML (href
    attributes only — no JS execution, no following of anything). Off-
    domain links found in the page are reported but never followed; this
    tool only ever performs the one GET on `path` itself.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("crawl_links", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("GET", url)
    except RuntimeError as e:
        logger.log_tool_call("crawl_links", path=path, url=url, method="GET", error=str(e))
        return {"error": str(e)}

    logger.log_tool_call(
        "crawl_links", path=path, url=url, method="GET", status=response.status_code,
    )

    same_domain_links = []
    external_links = []
    target_host = urlparse(config.TARGET_BASE_URL).hostname

    for href in _HREF_RE.findall(response.text or ""):
        absolute = urljoin(url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.hostname == target_host:
            if absolute not in same_domain_links:
                same_domain_links.append(absolute)
        else:
            if absolute not in external_links:
                external_links.append(absolute)
        if len(same_domain_links) >= max_links:
            break

    result = {
        "url": url,
        "status": response.status_code,
        "same_domain_links": same_domain_links[:max_links],
        "external_links_found": external_links[:max_links],
        "note": "External links are reported only, never fetched by this tool.",
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def _path_is_auto_approved(path: str) -> bool:
    if not path.startswith("/"):
        path = "/" + path
    return any(path.startswith(prefix) for prefix in config.AUTO_APPROVE_POST_PATHS)


def tool_reset_session() -> dict:
    """
    Clear the agent's persistent session (cookies accumulated from prior
    requests in this run, e.g. from a login on your own site). Does not
    affect the target site itself — only the agent's local session state.
    """
    global _session
    _session.close()
    _session = requests.Session()
    logger.log_event({"tool": "reset_session", "note": "Local session/cookies cleared."})
    return {"result": "Session cleared. Subsequent requests start with no cookies."}


def tool_get_sitemap() -> dict:
    """
    Fetch /robots.txt and /sitemap.xml from the target domain to help map
    out the site's structure before deciding what to inspect further.
    """
    result = {}
    for name, path in (("robots_txt", "/robots.txt"), ("sitemap_xml", "/sitemap.xml")):
        page = tool_get_page(path)
        if "error" in page:
            result[name] = {"error": page["error"]}
        else:
            result[name] = {"status": page["status"], "body": page["body"]}
    return result


def tool_check_options(path: str) -> dict:
    """
    Send an HTTP OPTIONS request to discover which methods a path allows
    (via the Allow response header), without performing any of those
    methods. Purely informational — never mutates anything.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("check_options", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("OPTIONS", url)
    except RuntimeError as e:
        logger.log_tool_call("check_options", path=path, url=url, method="OPTIONS", error=str(e))
        return {"error": str(e)}

    logger.log_tool_call(
        "check_options", path=path, url=url, method="OPTIONS",
        status=response.status_code, response_headers=dict(response.headers),
    )

    result = {
        "url": url,
        "status": response.status_code,
        "allowed_methods": response.headers.get("Allow", "(not reported)"),
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def tool_timing_check(path: str, samples: int = 3) -> dict:
    """
    GET the same path several times and report response time statistics.
    Useful for spotting slow endpoints or inconsistent performance. Each
    sample is a normal, fully-validated GET — same rate limiting applies.
    """
    if not isinstance(samples, int) or samples < 1:
        return {"error": "samples must be a positive integer."}
    samples = min(samples, 10)  # keep this bounded regardless of what's requested

    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("timing_check", str(e))
        return {"error": f"BLOCKED: {e}"}

    timings = []
    statuses = []
    for _ in range(samples):
        _enforce_rate_limit()
        start = time.time()
        try:
            response, _ = _safe_request("GET", url)
        except RuntimeError as e:
            return {"error": str(e), "completed_samples": timings}
        timings.append(round(time.time() - start, 4))
        statuses.append(response.status_code)

    logger.log_tool_call("timing_check", path=path, url=url, method="GET",
                          status=statuses[-1] if statuses else None)

    return {
        "url": url,
        "samples": samples,
        "timings_seconds": timings,
        "avg_seconds": round(sum(timings) / len(timings), 4),
        "min_seconds": min(timings),
        "max_seconds": max(timings),
        "statuses": statuses,
    }


# Security headers worth checking for presence/value, with a one-line note
# on what each protects against. This is a checklist, not a scanner for
# secrets — it only ever reports whether/what a header says, never content.
_SECURITY_HEADERS_TO_CHECK = {
    "Strict-Transport-Security": "Forces HTTPS on future visits (HSTS).",
    "Content-Security-Policy": "Restricts where scripts/styles/frames can load from (mitigates XSS).",
    "X-Frame-Options": "Prevents the page being embedded in a clickjacking iframe.",
    "X-Content-Type-Options": "Stops browsers from MIME-sniffing responses (should be 'nosniff').",
    "Referrer-Policy": "Controls how much of the URL is leaked to other sites via the Referer header.",
    "Permissions-Policy": "Restricts browser features (camera, mic, geolocation, etc.) the page can use.",
}


def tool_check_security_headers(path: str = "/") -> dict:
    """
    Fetch a page and report which common security-related response headers
    are present and what value they hold. Does not inspect cookies or body
    content — see check_cookie_flags for cookie-specific checks.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("check_security_headers", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("GET", url)
    except RuntimeError as e:
        logger.log_tool_call("check_security_headers", path=path, url=url, method="GET", error=str(e))
        return {"error": str(e)}

    logger.log_tool_call("check_security_headers", path=path, url=url, method="GET",
                          status=response.status_code)

    findings = {}
    for header, note in _SECURITY_HEADERS_TO_CHECK.items():
        value = response.headers.get(header)
        findings[header] = {
            "present": value is not None,
            "value": value,
            "purpose": note,
        }

    result = {"url": url, "status": response.status_code, "headers_checked": findings}
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def tool_check_cookie_flags(path: str = "/") -> dict:
    """
    Fetch a page and report, for each Set-Cookie header returned, which
    security flags (Secure, HttpOnly, SameSite) are set — WITHOUT ever
    reporting the cookie's name or value. This checks configuration only;
    it cannot be used to read or exfiltrate an actual session cookie.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("check_cookie_flags", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("GET", url)
    except RuntimeError as e:
        logger.log_tool_call("check_cookie_flags", path=path, url=url, method="GET", error=str(e))
        return {"error": str(e)}

    logger.log_tool_call("check_cookie_flags", path=path, url=url, method="GET",
                          status=response.status_code)

    cookie_reports = []
    # response.raw.headers may carry multiple Set-Cookie lines; requests
    # collapses response.headers to one, so pull from history-safe raw headers.
    raw_cookies = response.raw.headers.get_all("Set-Cookie") if response.raw and hasattr(response.raw.headers, "get_all") else None
    if raw_cookies is None:
        single = response.headers.get("Set-Cookie")
        raw_cookies = [single] if single else []

    for i, cookie_str in enumerate(raw_cookies):
        lowered = cookie_str.lower()
        cookie_reports.append({
            "cookie_index": i,  # never the name — flags only
            "secure": "secure" in lowered,
            "httponly": "httponly" in lowered,
            "samesite": (
                "strict" if "samesite=strict" in lowered else
                "lax" if "samesite=lax" in lowered else
                "none" if "samesite=none" in lowered else
                "not set"
            ),
        })

    result = {
        "url": url,
        "status": response.status_code,
        "cookies_found": len(cookie_reports),
        "cookie_flag_report": cookie_reports,
        "note": "Cookie names/values are never reported — flags only.",
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


_COMMON_EXPOSED_PATHS = [
    "/.env", "/.env.local", "/.env.production",
    "/.git/config", "/.git/HEAD",
    "/config.json", "/config.php", "/wp-config.php",
    "/.aws/credentials", "/id_rsa", "/.ssh/id_rsa",
    "/backup.sql", "/dump.sql", "/database.sql",
    "/.htpasswd", "/.htaccess",
    "/composer.json", "/package.json.bak",
    "/phpinfo.php", "/debug", "/.DS_Store",
]


def tool_check_exposed_files() -> dict:
    """
    HEAD-check a list of common accidentally-exposed file paths (.env,
    .git/config, backup dumps, etc.) and report only their HTTP status —
    NEVER their content. A 200 means something is publicly reachable at
    that path and worth locking down; the body is never fetched or stored.
    """
    results = {}
    for path in _COMMON_EXPOSED_PATHS:
        r = tool_head_page(path)
        if "error" in r:
            results[path] = {"error": r["error"]}
        else:
            results[path] = {
                "status": r["status"],
                "publicly_reachable": r["status"] == 200,
            }
    exposed = [p for p, r in results.items() if r.get("publicly_reachable")]
    return {
        "checked": len(_COMMON_EXPOSED_PATHS),
        "exposed_paths": exposed,
        "details": results,
        "note": "Only HTTP status was checked — file contents were never fetched.",
    }


def tool_check_ssl_certificate() -> dict:
    """
    Connect to the target domain on port 443 and report TLS certificate
    hygiene: issuer, subject, validity dates, days until expiry, and TLS
    protocol version negotiated. No content is fetched — this is a pure
    handshake-level check, the same kind of thing an uptime monitor does.
    """
    import ssl
    import socket
    from datetime import datetime, timezone

    hostname = urlparse(config.TARGET_BASE_URL).hostname
    if not hostname:
        return {"error": "TARGET_BASE_URL has no hostname configured."}

    # Reuse the same domain/IP validation as every other tool, so this
    # can't be pointed at anything but the configured target.
    try:
        security.validate_url(f"https://{hostname}/", context="ssl check")
    except security.SecurityError as e:
        logger.log_security_block("check_ssl_certificate", str(e))
        return {"error": f"BLOCKED: {e}"}

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((hostname, 443), timeout=config.REQUEST_TIMEOUT_SECONDS) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                tls_version = ssock.version()
    except Exception as e:
        logger.log_tool_call("check_ssl_certificate", url=hostname, error=str(e))
        return {"error": f"TLS connection failed: {e}"}

    not_after = cert.get("notAfter")
    days_left = None
    if not_after:
        try:
            expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days_left = (expiry - datetime.now(timezone.utc)).days
        except ValueError:
            pass

    def _flatten(name_tuple):
        return {k: v for entry in name_tuple for k, v in entry}

    result = {
        "hostname": hostname,
        "tls_version": tls_version,
        "issuer": _flatten(cert.get("issuer", ())),
        "subject": _flatten(cert.get("subject", ())),
        "valid_from": cert.get("notBefore"),
        "valid_until": not_after,
        "days_until_expiry": days_left,
        "expiring_soon": days_left is not None and days_left < 30,
    }
    logger.log_tool_call("check_ssl_certificate", url=hostname, status=None)
    return result


def tool_check_cors_policy(path: str = "/") -> dict:
    """
    Send a GET request with a foreign Origin header and report what the
    server's CORS response headers say. An Access-Control-Allow-Origin of
    '*' combined with Access-Control-Allow-Credentials: true is a known
    misconfiguration worth flagging. Read-only — no state is changed.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("check_cors_policy", str(e))
        return {"error": f"BLOCKED: {e}"}

    _enforce_rate_limit()

    probe_origin = "https://cors-probe.invalid"
    try:
        response, redirect_info = _safe_request(
            "GET", url, custom_headers={"Origin": probe_origin}
        )
    except RuntimeError as e:
        logger.log_tool_call("check_cors_policy", path=path, url=url, method="GET", error=str(e))
        return {"error": str(e)}

    allow_origin = response.headers.get("Access-Control-Allow-Origin")
    allow_credentials = response.headers.get("Access-Control-Allow-Credentials")
    misconfigured = allow_origin == "*" and str(allow_credentials).lower() == "true"

    logger.log_tool_call("check_cors_policy", path=path, url=url, method="GET",
                          status=response.status_code)

    result = {
        "url": url,
        "status": response.status_code,
        "probe_origin_sent": probe_origin,
        "access_control_allow_origin": allow_origin,
        "access_control_allow_credentials": allow_credentials,
        "reflects_arbitrary_origin": allow_origin == probe_origin,
        "risky_wildcard_with_credentials": misconfigured,
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


_COMMON_DIRS_TO_CHECK = [
    "/", "/admin/", "/uploads/", "/backup/", "/backups/", "/logs/",
    "/tmp/", "/static/", "/assets/", "/images/", "/files/", "/data/",
    "/.git/", "/api/", "/config/", "/private/",
]


def tool_check_directory_listing() -> dict:
    """
    GET a list of common directory paths and flag any that appear to have
    directory listing enabled (index-of-style autogenerated listings),
    which can leak file names that shouldn't be public. Only the first
    part of each body is scanned for the tell-tale pattern — full listings
    are never stored.
    """
    listing_markers = ("index of /", "<title>index of", "parent directory")
    results = {}
    for path in _COMMON_DIRS_TO_CHECK:
        r = tool_get_page(path)
        if "error" in r:
            results[path] = {"error": r["error"]}
            continue
        body_lower = (r.get("body") or "")[:2000].lower()
        looks_like_listing = any(marker in body_lower for marker in listing_markers)
        results[path] = {
            "status": r["status"],
            "looks_like_directory_listing": looks_like_listing,
        }
    flagged = [p for p, r in results.items() if r.get("looks_like_directory_listing")]
    return {
        "checked": len(_COMMON_DIRS_TO_CHECK),
        "flagged_paths": flagged,
        "details": results,
        "note": "Detection is a text-pattern heuristic — verify flagged paths manually.",
    }


def _confirm_post(url: str, data: dict, path: str) -> bool:
    """
    TESTING MODE: confirmation prompt DISABLED. Every POST/PUT/PATCH/DELETE
    is auto-approved. The request details are still printed so you can see
    what the agent is doing in real time.

    ⚠️  In this mode, a mistaken or malicious task description can modify
    or delete data on the target site with no opportunity to intervene.
    Check logs/agent.log after every task if anything looks unexpected.
    """
    print("\n" + "=" * 50)
    print("REQUEST (CONFIRMATION DISABLED — auto-approved)")
    print(f"URL: {url}")
    print(f"DATA: {json.dumps(data, indent=2)}")
    print("=" * 50)
    return True


def tool_post_json(path: str, data: dict) -> dict:
    """
    POST tool: send a JSON body to the configured target domain.
    TESTING MODE: auto-approved, no confirmation prompt.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("post_json", str(e))
        return {"error": f"BLOCKED: {e}"}

    if not isinstance(data, dict):
        return {"error": "POST data must be a JSON object."}

    try:
        raw_body = json.dumps(data).encode("utf-8")
        security.enforce_post_body_size(raw_body)
    except security.SecurityError as e:
        logger.log_security_block("post_json", str(e))
        return {"error": f"BLOCKED: {e}"}

    approved = _confirm_post(url, data, path)
    if not approved:
        logger.log_tool_call("post_json", path=path, url=url, method="POST",
                              request_body=data, approved=False)
        return {"error": "POST rejected by human operator."}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("POST", url, json_body=data)
    except RuntimeError as e:
        logger.log_tool_call("post_json", path=path, url=url, method="POST",
                              request_body=data, approved=True, error=str(e))
        return {"error": str(e)}

    body = _truncate(response.text, config.MAX_RESPONSE_BYTES)

    logger.log_tool_call(
        "post_json",
        path=path,
        url=url,
        method="POST",
        status=response.status_code,
        request_body=data,
        response_headers=dict(response.headers),
        approved=True,
    )

    result = {
        "url": url,
        "final_url": response.url,
        "status": response.status_code,
        "body": body,
    }
    if redirect_info:
        result["redirect"] = redirect_info
    return result


def tool_post_form(path: str, fields: dict) -> dict:
    """
    Form POST tool: send an application/x-www-form-urlencoded body — the
    format traditional HTML <form> submissions use, as opposed to JSON
    APIs. TESTING MODE: auto-approved, no confirmation prompt.
    """
    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("post_form", str(e))
        return {"error": f"BLOCKED: {e}"}

    if not isinstance(fields, dict):
        return {"error": "fields must be a JSON object of string key/value pairs."}

    try:
        raw_body = "&".join(f"{k}={v}" for k, v in fields.items()).encode("utf-8")
        security.enforce_post_body_size(raw_body)
    except security.SecurityError as e:
        logger.log_security_block("post_form", str(e))
        return {"error": f"BLOCKED: {e}"}

    approved = _confirm_post(url, fields, path)
    if not approved:
        logger.log_tool_call("post_form", path=path, url=url, method="POST",
                              request_body=fields, approved=False)
        return {"error": "POST rejected by human operator."}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request("POST", url, form_body=fields)
    except RuntimeError as e:
        logger.log_tool_call("post_form", path=path, url=url, method="POST",
                              request_body=fields, approved=True, error=str(e))
        return {"error": str(e)}

    body = _truncate(response.text, config.MAX_RESPONSE_BYTES)

    logger.log_tool_call(
        "post_form",
        path=path,
        url=url,
        method="POST",
        status=response.status_code,
        request_body=fields,
        response_headers=dict(response.headers),
        approved=True,
    )

    result = {"url": url, "final_url": response.url, "status": response.status_code, "body": body}
    if redirect_info:
        result["redirect"] = redirect_info
    return result


_MUTATING_METHODS = {"PUT", "PATCH", "DELETE"}


def tool_modify_resource(method: str, path: str, data: dict = None) -> dict:
    """
    PUT/PATCH/DELETE tool: send a mutating request to a path on the
    configured target domain. TESTING MODE: auto-approved, no confirmation
    prompt.

    ⚠️  A DELETE or PUT against a real path will actually execute. There
    is no safety net in this mode.
    """
    method = (method or "").upper()
    if method not in _MUTATING_METHODS:
        return {"error": f"Unsupported method '{method}'. Use PUT, PATCH, or DELETE."}

    try:
        url = security.build_target_url(path)
    except security.SecurityError as e:
        logger.log_security_block("modify_resource", str(e))
        return {"error": f"BLOCKED: {e}"}

    data = data or {}
    if not isinstance(data, dict):
        return {"error": "data must be a JSON object (or omitted for DELETE)."}

    try:
        raw_body = json.dumps(data).encode("utf-8")
        security.enforce_post_body_size(raw_body)
    except security.SecurityError as e:
        logger.log_security_block("modify_resource", str(e))
        return {"error": f"BLOCKED: {e}"}

    approved = _confirm_post(url, data, path)
    if not approved:
        logger.log_tool_call("modify_resource", path=path, url=url, method=method,
                              request_body=data, approved=False)
        return {"error": f"{method} rejected by human operator."}

    _enforce_rate_limit()

    try:
        response, redirect_info = _safe_request(method, url, json_body=data if data else None)
    except RuntimeError as e:
        logger.log_tool_call("modify_resource", path=path, url=url, method=method,
                              request_body=data, approved=True, error=str(e))
        return {"error": str(e)}

    body = _truncate(response.text, config.MAX_RESPONSE_BYTES)

    logger.log_tool_call(
        "modify_resource",
        path=path,
        url=url,
        method=method,
        status=response.status_code,
        request_body=data,
        response_headers=dict(response.headers),
        approved=True,
    )

    result = {"url": url, "final_url": response.url, "status": response.status_code, "body": body}
    if redirect_info:
        result["redirect"] = redirect_info
    return result


# ---------------------------------------------------------------------------
# Tool schema exposed to the LLM (provider-agnostic JSON schema; each
# provider adapter converts this into its own function-calling format).
# UNCHANGED from the original — the LLM still sees the same tool list.
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS = [
    {
        "name": "get_page",
        "description": (
            "Send an HTTP GET request to a path on the configured target "
            "website (e.g. '/', '/about', '/api/status'). Use this to "
            "inspect pages and read-only API endpoints. Optional custom "
            "headers may be sent (e.g. Accept, Content-Type)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path on the target site, e.g. '/api/status'.",
                },
                "headers": {
                    "type": "object",
                    "description": "Optional extra request headers as key/value string pairs.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "batch_get",
        "description": (
            "Send multiple GET requests in one call, one per path. Useful "
            "for inspecting several pages/endpoints without round-tripping "
            "through the model for each one. Same validation and rate "
            "limiting applies to each path as a single get_page call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of paths to GET, e.g. ['/', '/about', '/api/status'].",
                }
            },
            "required": ["paths"],
        },
    },
    {
        "name": "post_json",
        "description": (
            "Send an HTTP POST request with a JSON body to a path on the "
            "configured target website. Requires human approval in the "
            "terminal unless the path matches AUTO_APPROVE_POST_PATHS."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path on the target site, e.g. '/api/agent-test'.",
                },
                "data": {
                    "type": "object",
                    "description": "JSON-serializable object to send as the POST body.",
                },
            },
            "required": ["path", "data"],
        },
    },
    {
        "name": "modify_resource",
        "description": (
            "Send a PUT, PATCH, or DELETE request to a path on the "
            "configured target website. Requires human approval in the "
            "terminal unless the path matches AUTO_APPROVE_POST_PATHS, "
            "exactly like post_json."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["PUT", "PATCH", "DELETE"],
                    "description": "HTTP method to use.",
                },
                "path": {
                    "type": "string",
                    "description": "Path on the target site, e.g. '/api/items/42'.",
                },
                "data": {
                    "type": "object",
                    "description": "Optional JSON body (commonly omitted for DELETE).",
                },
            },
            "required": ["method", "path"],
        },
    },
    {
        "name": "head_page",
        "description": (
            "Send an HTTP HEAD request to a path — returns status and "
            "headers only, no body. Fast way to check if an endpoint "
            "exists, its content type/size, or caching headers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path on the target site, e.g. '/large-file.pdf'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "crawl_links",
        "description": (
            "Fetch a page and extract the links found in its HTML. "
            "Same-domain links are listed for further inspection; "
            "off-domain links are reported but never fetched. Use this "
            "to map out a site's structure before deciding what to "
            "inspect next with get_page or batch_get."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path of the page to extract links from, e.g. '/'.",
                },
                "max_links": {
                    "type": "integer",
                    "description": "Maximum number of same-domain links to return (default 30).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "post_form",
        "description": (
            "Send an HTTP POST with an application/x-www-form-urlencoded "
            "body (the format traditional HTML forms submit), rather than "
            "JSON. Same human-approval gate as post_json."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path on the target site, e.g. '/contact'.",
                },
                "fields": {
                    "type": "object",
                    "description": "Form field name/value pairs to submit.",
                },
            },
            "required": ["path", "fields"],
        },
    },
    {
        "name": "reset_session",
        "description": (
            "Clear the agent's local session (cookies accumulated from "
            "prior requests in this run, e.g. after a login). Does not "
            "affect the target site — only local session state."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_sitemap",
        "description": (
            "Fetch /robots.txt and /sitemap.xml from the target domain in "
            "one call, to help discover the site's structure and any "
            "disallowed paths before further inspection."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_options",
        "description": (
            "Send an HTTP OPTIONS request to a path to discover which "
            "HTTP methods it allows (via the Allow header), without "
            "performing any of those methods. Purely informational."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to check, e.g. '/api/status'.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "timing_check",
        "description": (
            "GET the same path multiple times (up to 10) and report "
            "response time statistics (min/max/avg) and status codes. "
            "Useful for spotting slow or inconsistent endpoints."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to measure, e.g. '/'.",
                },
                "samples": {
                    "type": "integer",
                    "description": "Number of GET requests to time (1-10, default 3).",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "check_security_headers",
        "description": (
            "Fetch a page and report which common security-related "
            "response headers (HSTS, CSP, X-Frame-Options, etc.) are "
            "present and what value they hold. Configuration check only — "
            "never inspects cookies or body content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to check, e.g. '/' (default).",
                }
            },
        },
    },
    {
        "name": "check_cookie_flags",
        "description": (
            "Fetch a page and report which security flags (Secure, "
            "HttpOnly, SameSite) are set on each cookie the server "
            "returns. Cookie names and values are never reported — flags "
            "only. Cannot be used to read or exfiltrate cookie contents."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to check, e.g. '/' or '/login' (default '/').",
                }
            },
        },
    },
    {
        "name": "check_exposed_files",
        "description": (
            "Check a list of commonly-misconfigured paths (.env, "
            "/.git/config, backup .sql dumps, wp-config.php, etc.) to see "
            "if any are publicly reachable. Only checks HTTP status via "
            "HEAD requests — file contents are never fetched or stored."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_ssl_certificate",
        "description": (
            "Connect to the target domain on port 443 and report TLS "
            "certificate hygiene: issuer, subject, validity dates, days "
            "until expiry, and negotiated TLS version. Handshake-level "
            "only — no content is fetched."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "check_cors_policy",
        "description": (
            "Send a GET request with a foreign Origin header and report "
            "the server's CORS response headers, flagging a wildcard "
            "Access-Control-Allow-Origin combined with "
            "Access-Control-Allow-Credentials: true (a known "
            "misconfiguration). Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to check, e.g. '/' (default) or '/api/data'.",
                }
            },
        },
    },
    {
        "name": "check_directory_listing",
        "description": (
            "Check a list of common directory paths (/admin/, /uploads/, "
            "/backup/, /.git/, etc.) for signs that directory listing is "
            "enabled, which can leak file names. Heuristic text-pattern "
            "check only — flagged paths should be verified manually."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]

TOOL_IMPLEMENTATIONS = {
    "get_page": lambda args: tool_get_page(args.get("path", ""), args.get("headers")),
    "batch_get": lambda args: tool_batch_get(args.get("paths", [])),
    "post_json": lambda args: tool_post_json(args.get("path", ""), args.get("data", {})),
    "modify_resource": lambda args: tool_modify_resource(
        args.get("method", ""), args.get("path", ""), args.get("data")
    ),
    "head_page": lambda args: tool_head_page(args.get("path", "")),
    "crawl_links": lambda args: tool_crawl_links(args.get("path", ""), args.get("max_links", 30)),
    "post_form": lambda args: tool_post_form(args.get("path", ""), args.get("fields", {})),
    "reset_session": lambda args: tool_reset_session(),
    "get_sitemap": lambda args: tool_get_sitemap(),
    "check_options": lambda args: tool_check_options(args.get("path", "")),
    "timing_check": lambda args: tool_timing_check(args.get("path", ""), args.get("samples", 3)),
    "check_security_headers": lambda args: tool_check_security_headers(args.get("path", "/")),
    "check_cookie_flags": lambda args: tool_check_cookie_flags(args.get("path", "/")),
    "check_exposed_files": lambda args: tool_check_exposed_files(),
    "check_ssl_certificate": lambda args: tool_check_ssl_certificate(),
    "check_cors_policy": lambda args: tool_check_cors_policy(args.get("path", "/")),
    "check_directory_listing": lambda args: tool_check_directory_listing(),
}
"""
agent.py — CLI entry point and the tool-calling agent loop.

⚠️  TESTING MODE — enforcement has been DISABLED in security.py and the
    confirmation prompt has been disabled in tools.py. The system prompt
    below now describes the agent's FULL capabilities rather than its
    restrictions, so the model knows what it can do.

    The Python layer is still the place where requests are executed, but
    the domain lock, private-IP block, scheme block, and human-approval
    gate are all currently OFF. Do not leave this configuration running
    against any site you do not own.

Loop:
    user task -> LLM -> LLM chooses tool -> Python executes ->
    result back to LLM -> LLM reasons -> another tool call OR final answer.
"""

import sys

import config
import llm_provider
import tools

SYSTEM_PROMPT = f"""You are a site-inspection and QA agent performing authorized testing
against a website on behalf of its owner.

Environment facts — what you can actually do in this run:
- Your primary target is: {config.TARGET_BASE_URL or '<not configured>'}
- The Python security layer that normally restricts you to a single domain,
  blocks private/internal IPs, blocks file:// / javascript: / data: URLs,
  and validates redirects has been DISABLED in this run. You are not
  restricted to the target domain — you may request any http:// or https://
  URL, including other public websites and (if reachable) localhost or
  private-network addresses.
- POST, PUT, PATCH, and DELETE requests (post_json, post_form,
  modify_resource) do NOT require human approval in this run. They execute
  immediately. The request URL and body are still printed to the terminal
  for visibility, but there is no y/N prompt.
- Redirects are followed automatically by the HTTP client. The URL you
  finally landed on is reported back to you as `final_url` in the result.
- Your only hard limits are technical: HTTP/HTTPS schemes only (the
  `requests` library cannot speak file:// or javascript:), request
  timeouts, response/POST size caps, and a limit on total tool calls per
  task. Everything else is your judgment.

Your toolset, grouped by purpose:

Content & structure inspection (read-only, execute automatically):
- get_page / batch_get — fetch one or many pages/endpoints and return
  status, headers, and body. Custom headers can be sent (Host and
  Content-Length are still blocked by the HTTP layer).
- head_page — status/headers only, no body; fast existence/type checks.
- crawl_links — fetch a page and list links found in its HTML. Same-domain
  links are grouped separately from external ones for convenience, but
  there is no rule preventing you from fetching an external link in a
  subsequent call if the task requires it.
- get_sitemap — fetch /robots.txt and /sitemap.xml in one call.
- check_options — discover allowed HTTP methods on a path via OPTIONS.
- timing_check — repeat a GET up to 10 times and report response-time stats.
- reset_session — clear locally-held cookies; no network call.

Configuration & hardening checks (read-only, report status/config only —
never fetch, store, or reason about actual secret values):
- check_security_headers — presence/value of HSTS, CSP, X-Frame-Options, etc.
- check_cookie_flags — Secure/HttpOnly/SameSite flags only; never cookie
  names or values.
- check_exposed_files — HEAD-only checks for commonly-leaked paths
  (.env, .git, backups); status code only, contents never fetched.
- check_ssl_certificate — TLS handshake metadata: issuer, validity,
  protocol version. No content fetched.
- check_cors_policy — flags risky wildcard-origin + credentials CORS
  configurations.
- check_directory_listing — heuristic check for exposed directory listings.

State-changing actions (execute immediately in this run — no prompt):
- post_json / post_form — submit data to an endpoint.
- modify_resource — PUT/PATCH/DELETE against a resource.

Behavioral guidance (not enforced — use good judgment):
- Briefly explain what you are about to do before each tool call, so the
  operator can follow along in the terminal.
- Prefer read-only inspection before proposing any state-changing action.
  A DELETE or PUT against real data is irreversible in this run.
- If the task specifically asks you to look for credentials, secrets, or
  tokens in responses, you may do so — but be aware that anything written
  to the terminal or to logs/agent.log is visible to anyone with access to
  this machine. Treat anything sensitive you encounter as such.
- When you have enough information, give a clear FINAL answer summarizing
  what you found, organized by what was inspected and what (if anything)
  needs the site owner's attention. Do not keep calling tools once you
  have your answer.
"""


def run_task(user_task: str) -> None:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_task},
    ]

    tool_call_count = 0

    while True:
        if tool_call_count >= config.MAX_TOOL_CALLS_PER_TASK:
            print("\n[Agent stopped: reached MAX_TOOL_CALLS_PER_TASK limit.]")
            break

        try:
            response = llm_provider.chat(messages)
        except RuntimeError as e:
            print(f"\n[LLM error: {e}]")
            break

        assistant_text = response.get("text")
        requested_tool_calls = response.get("tool_calls") or []

        if assistant_text:
            print(f"\nAgent:\n{assistant_text}")

        if not requested_tool_calls:
            # No more tool calls requested — this is the final answer.
            break

        # Record the assistant's turn (text + tool call requests) in history.
        messages.append({
            "role": "assistant",
            "content": assistant_text,
            "tool_calls": requested_tool_calls,
        })

        for call in requested_tool_calls:
            tool_call_count += 1
            if tool_call_count > config.MAX_TOOL_CALLS_PER_TASK:
                messages.append({
                    "role": "tool",
                    "name": call["name"],
                    "content": {"error": "Tool call limit reached for this task."},
                })
                continue

            name = call["name"]
            args = call["arguments"]
            impl = tools.TOOL_IMPLEMENTATIONS.get(name)

            # Show method too, for modify_resource and friends.
            display = args.get("path", "")
            if name == "modify_resource":
                display = f"{args.get('method', '')} {display}".strip()
            print(f"\nTOOL: {name.upper()} {display}")

            if impl is None:
                result = {"error": f"Unknown tool '{name}'."}
            else:
                result = impl(args)

            status = result.get("status")
            if status is not None:
                print(f"STATUS: {status}")
            elif "error" in result:
                print(f"ERROR: {result['error']}")

            messages.append({
                "role": "tool",
                "name": name,
                "content": result,
            })

    print("\n" + "-" * 40)


def print_banner():
    print("=" * 52)
    print(" Controlled Web AI Agent  —  TESTING MODE")
    print("=" * 52)
    print(f"\nProvider: {config.LLM_PROVIDER}  Model: {config.MODEL}")
    print(f"Target: {config.TARGET_BASE_URL or '(NOT CONFIGURED — set TARGET_BASE_URL in .env)'}")

    def onoff(flag: bool) -> str:
        return "ON" if flag else "OFF"

    print("\n-- Toggles (set in .env) -----------------------")
    print(f"  Confirmation prompt (POST/PUT/PATCH/DELETE): {onoff(config.ENABLE_CONFIRMATION_PROMPT)}")
    print(f"  Rate limiting:                                {onoff(config.ENABLE_RATE_LIMIT)}")
    print(f"  Redirect confirmation (off = auto-follow):    {onoff(config.ENABLE_REDIRECT_CONFIRMATION)}")

    print("\n-- Enforcement status (testing mode) -----------")
    print("  Single-domain lock:                           OFF")
    print("  Private/metadata IP block:                    OFF")
    print("  file:// / javascript: / data: scheme block:   OFF")
    print("  Redirect target validation:                   OFF")
    print("  POST/PUT/PATCH/DELETE confirmation prompt:    OFF")
    print("  Sensitive-data redaction in logs:             ON")
    print("  POST body size cap:                           ON")
    print("  Host / Content-Length header block:           ON")
    print("-" * 52)
    print("  ⚠️  TESTING MODE — not safe for production use.")
    print("     Check logs/agent.log after every task.")
    print("-" * 52)


def main():
    print_banner()

    if not config.TARGET_BASE_URL:
        print("\nERROR: TARGET_BASE_URL is not set. Configure your .env file and retry.")
        sys.exit(1)

    print("\nEnter your task (or 'quit' to exit):")
    while True:
        try:
            task = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not task:
            continue
        if task.lower() in ("quit", "exit"):
            break

        run_task(task)
        print("\nEnter your next task (or 'quit' to exit):")


if __name__ == "__main__":
    main()
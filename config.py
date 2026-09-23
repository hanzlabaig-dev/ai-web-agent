"""
config.py — Central configuration and safety limits.

All tunable safety limits live here so they are easy to audit and change
in one place. Nothing in this file executes network requests.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Provider configuration (swappable: gemini | openrouter | grok)
# ---------------------------------------------------------------------------
# LLM_PROVIDER selects which free-tier provider to use. The agent loop and
# tool-calling logic are provider-agnostic; only llm_provider.py needs to
# change if you add a new provider.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

# Generic model name — meaning depends on provider (see llm_provider.py):
#   gemini     -> e.g. "gemini-1.5-flash" / "gemini-2.0-flash-exp"
#   openrouter -> e.g. "meta-llama/llama-3.1-8b-instruct:free"
#   grok       -> e.g. "grok-beta" (xAI free/trial tier, if available)
MODEL = os.getenv("MODEL", "gemini-1.5-flash")

# API keys — only the one matching LLM_PROVIDER needs to be set.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROK_API_KEY = os.getenv("GROK_API_KEY", "")

# ---------------------------------------------------------------------------
# Target website — the ONLY domain the agent may ever contact
# ---------------------------------------------------------------------------
TARGET_BASE_URL = os.getenv("TARGET_BASE_URL", "").strip()

# ---------------------------------------------------------------------------
# Safety limits (all enforced in Python, never trusted to the LLM)
# ---------------------------------------------------------------------------
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
MAX_RESPONSE_BYTES = int(os.getenv("MAX_RESPONSE_BYTES", "100000"))          # truncate response bodies
MAX_TOOL_CALLS_PER_TASK = int(os.getenv("MAX_TOOL_CALLS_PER_TASK", "30"))
MAX_POST_BODY_BYTES = int(os.getenv("MAX_POST_BODY_BYTES", "50000"))
MIN_SECONDS_BETWEEN_REQUESTS = float(os.getenv("MIN_SECONDS_BETWEEN_REQUESTS", "0.3"))

LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "logs/agent.log")

# ---------------------------------------------------------------------------
# TOGGLE PANEL — explicit ON/OFF switches for the settings that are safe to
# adjust. Each one is named ENABLE_* so its state is unambiguous. These only
# affect confirmation prompts, rate limiting, and redirect handling.
#
# NOT included here, and NOT toggleable by any setting, ever:
#   - the single-domain lock (security.py: validate_url / build_target_url)
#   - the private/loopback/link-local/reserved IP block, which also covers
#     cloud metadata endpoints (security.py: _is_private_or_dangerous_ip)
#   - the file:// / javascript: / data: scheme block
#   - the system prompt's refusal of credential harvesting, auth bypass,
#     and exploitation techniques (agent.py)
# These stay hardcoded regardless of provider, domain, or any .env value,
# because they're what makes the domain lock actually hold under redirects
# and DNS tricks rather than being cosmetic.
# ---------------------------------------------------------------------------

# ON  -> every POST/PUT/PATCH/DELETE prints the request and waits for y/N.
# OFF -> those requests fire immediately with no prompt (still domain-locked).
ENABLE_CONFIRMATION_PROMPT = os.getenv("ENABLE_CONFIRMATION_PROMPT", "false").strip().lower() == "true"

# ON  -> agent waits MIN_SECONDS_BETWEEN_REQUESTS between requests.
# OFF -> no delay between requests.
ENABLE_RATE_LIMIT = os.getenv("ENABLE_RATE_LIMIT", "false").strip().lower() == "true"

# ON  -> a same-domain redirect is reported back, requiring a fresh tool call.
# OFF -> same-domain redirects (only) are followed automatically.
ENABLE_REDIRECT_CONFIRMATION = os.getenv("ENABLE_REDIRECT_CONFIRMATION", "false").strip().lower() == "true"
AUTO_FOLLOW_REDIRECTS = not ENABLE_REDIRECT_CONFIRMATION
AUTO_REDIRECT_MAX_HOPS = int(os.getenv("AUTO_REDIRECT_MAX_HOPS", "5"))

# Path prefixes that skip the confirmation prompt even when
# ENABLE_CONFIRMATION_PROMPT=true (useful for approving just one or two
# test endpoints while keeping the prompt on for everything else).
# "/" matches every path. Ignored entirely when the prompt is OFF.
_auto_approve_raw = os.getenv("AUTO_APPROVE_POST_PATHS", "/")
AUTO_APPROVE_POST_PATHS = [p.strip() for p in _auto_approve_raw.split(",") if p.strip()]

# Headers that must never be logged or shown, regardless of source.
SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}

SENSITIVE_JSON_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "otp",
}

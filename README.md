# Controlled Web AI Agent

An educational project for studying how an LLM-driven agent behaves when given
real (but tightly scoped) internet access via tool calling. The agent can
**only** talk to one website — the one you configure — and every safety
control is enforced in Python, not in the prompt.

Free-tier LLM providers supported out of the box: **Gemini**, **OpenRouter**,
**Grok**. Swap providers by changing two lines in `.env`.

---

## 1. What is an AI agent?

A plain LLM takes text in, produces text out. It has no way to *do* anything
in the world on its own. An **agent** wraps an LLM with:

- **Tools** it can request to use (here: `get_page`, `post_json`)
- A **loop** that executes those tool requests, feeds the results back to
  the model, and lets it decide what to do next
- Enough turns of this loop to complete a multi-step task autonomously

## 2. LLM vs. agent

| | LLM alone | Agent |
|---|---|---|
| Input/output | Text in, text out | Text in, **actions + text** out |
| State | Single turn | Multi-turn loop with memory of tool results |
| Effect on the world | None | Can fetch pages, submit data (with approval), etc. |

The LLM never touches the network directly. It only ever *asks* for a tool
call (as structured JSON); Python decides whether to honor that request.

## 3. How tools work here

Seventeen tools are exposed to the model:

| Tool | Method(s) | Confirmation needed? |
|---|---|---|
| `get_page` | GET (optional custom headers) | No |
| `batch_get` | Multiple GETs in one call | No |
| `head_page` | HEAD (status/headers only, no body) | No |
| `crawl_links` | GET + extracts same-domain links from the HTML | No |
| `get_sitemap` | GET `/robots.txt` + `/sitemap.xml` in one call | No |
| `check_options` | OPTIONS (discovers allowed methods, performs none of them) | No |
| `timing_check` | GET the same path 1-10 times, reports response-time stats | No |
| `check_security_headers` | GET, reports HSTS/CSP/X-Frame-Options/etc. presence | No |
| `check_cookie_flags` | GET, reports Secure/HttpOnly/SameSite flags (never names/values) | No |
| `check_exposed_files` | HEAD-checks common leak paths (.env, .git, backups) — status only | No |
| `check_ssl_certificate` | TLS handshake only — issuer, expiry, protocol version | No |
| `check_cors_policy` | GET with a foreign Origin header — flags risky wildcard+credentials CORS | No |
| `check_directory_listing` | GET common dirs, flags index-of-style listings | No |
| `reset_session` | Clears the agent's local cookies (no network call) | No |
| `post_json` | POST (JSON body) | Yes, unless path is in `AUTO_APPROVE_POST_PATHS` |
| `post_form` | POST (form-urlencoded body) | Yes, unless path is in `AUTO_APPROVE_POST_PATHS` |
| `modify_resource` | PUT / PATCH / DELETE | Yes, unless path is in `AUTO_APPROVE_POST_PATHS` |

All GET-family and mutating requests share one `requests.Session()`, so
cookies set by your own site (e.g. after a login POST) persist across
subsequent tool calls in the same run — useful for testing logged-in
flows on your own site. Use `reset_session` to clear it mid-conversation.

Each is described to the model as a JSON schema (name, description,
parameters). Python then:

1. Validates the request (`security.py`) — is this URL actually inside the
   allowed domain? Does it resolve to a public, non-private IP?
2. Executes the request if valid (`tools.py`)
3. Redacts anything sensitive (headers, JSON keys) before logging or
   returning the result
4. Feeds the result back to the model as a `tool` message

## 4. How the agent loop works

```
user task
   ↓
LLM
   ↓
LLM chooses a tool (or gives a final answer)
   ↓
Python validates the tool call  ← security.py is the real gate
   ↓
tool executes (or is rejected)
   ↓
result returned to LLM
   ↓
LLM reasons about the result
   ↓
another tool call OR final answer
```

This repeats until the model stops requesting tools, or
`MAX_TOOL_CALLS_PER_TASK` is reached (default 12), whichever comes first.

## 5. Installation

```bash
cd ai-agent
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## 6. `.env` configuration

Open `.env` and set:

```ini
LLM_PROVIDER=gemini          # gemini | openrouter | grok
MODEL=gemini-1.5-flash
GEMINI_API_KEY=your-key-here
TARGET_BASE_URL=https://yourwebsite.com
```

Only fill in the API key for the provider you selected.

**Getting free API keys:**
- **Gemini** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free tier available.
- **OpenRouter** — [openrouter.ai/keys](https://openrouter.ai/keys) — browse models filtered to `:free` at openrouter.ai/models.
- **Grok (xAI)** — [console.x.ai](https://console.x.ai) — check current free/trial credit terms; pricing and availability change, so verify on their site.

`TARGET_BASE_URL` is the **only** domain the agent will ever be allowed to
contact. There is no way to override this from within a conversation with
the model — it's enforced in `security.py`, before any request is made.

## 7. Running the agent

```bash
python agent.py
```

You'll see:

```
========================================
 Controlled Web AI Agent
========================================

Provider: gemini  Model: gemini-1.5-flash
Target: https://yourwebsite.com

Enter your task (or 'quit' to exit):
>
```

Type a natural-language task and press Enter.

## 8. Creating the safe test endpoint

To experiment with POST requests without touching production data, add a
harmless echo endpoint to your own site. Example (Node/Express):

```javascript
app.post('/api/agent-test', (req, res) => {
  res.json({ success: true, received: req.body });
});
```

Example (Flask):

```python
@app.route('/api/agent-test', methods=['POST'])
def agent_test():
    return jsonify({"success": True, "received": request.get_json(silent=True)})
```

This endpoint reads and echoes input only — it does not write to your
database or modify anything, so it's safe to let the agent test POST
requests against it.

## 9. GET experiments

Try:

```
> Inspect my homepage and tell me what technologies you can detect from the headers.
> Check /api/status and tell me if it's returning valid JSON.
```

The agent will issue `GET` tool calls automatically and reason over the
results — no confirmation needed for GETs, since they're read-only.

## 10. POST experiments

Try:

```
> Send a test POST to /api/agent-test with the message "hello from the agent"
```

You'll see a confirmation prompt in the terminal **every single time**:

```
==================================================
POST REQUEST
URL: https://yourwebsite.com/api/agent-test
DATA: {
  "message": "hello from the agent"
}
==================================================
Allow this POST? [y/N]:
```

Nothing is sent unless you type `y`. There is no setting that auto-approves
POSTs — this is intentional.

## 11. Reading logs

All tool activity is logged as JSON lines in `logs/agent.log`:

```json
{"tool": "get_page", "path": "/", "url": "https://yourwebsite.com/", "method": "GET", "status": 200, "timestamp": "..."}
{"tool": "post_json", "path": "/api/agent-test", "request_body": {"message": "hello"}, "approved": true, "status": 200, "timestamp": "..."}
```

Secrets are **never** written to this file: `Authorization`/`Cookie`/`Set-Cookie`
headers and JSON keys like `password`, `token`, `api_key`, `otp`, etc. are
replaced with `***REDACTED***` before logging (see `security.py`'s
`redact_headers` / `redact_json_body`, applied in `logger.py`).

Blocked requests are also logged, e.g.:

```json
{"tool": "get_page", "blocked": true, "reason": "Blocked request to 'evil.com' — only 'yourwebsite.com' is permitted.", "timestamp": "..."}
```

## 12. Toggle panel — on/off switches

Three things are genuinely optional and controlled by explicit `ENABLE_*`
switches in `.env`. Running `python agent.py` prints their current state
in the startup banner so you always know what's on before you type a task.

| Switch | Default | ON does | OFF does |
|---|---|---|---|
| `ENABLE_CONFIRMATION_PROMPT` | `false` | Every POST/PUT/PATCH/DELETE prints the request and waits for `y/N` | Those requests fire immediately, no prompt |
| `ENABLE_RATE_LIMIT` | `false` | Waits `MIN_SECONDS_BETWEEN_REQUESTS` between requests | No delay between requests |
| `ENABLE_REDIRECT_CONFIRMATION` | `false` | Same-domain redirects are reported back, requiring a fresh tool call | Same-domain redirects are followed automatically |

`AUTO_APPROVE_POST_PATHS` (default `/`) only matters when
`ENABLE_CONFIRMATION_PROMPT=true` — it's a comma-separated list of path
prefixes that skip the prompt anyway, so you can turn the prompt back on
generally but still auto-approve one or two test endpoints.

Also relaxed by default vs. a strict baseline: higher request/response
limits (30s timeout, 5MB response/POST body caps, 100 tool-calls/task —
all overridable in `.env`), and extra tools: `batch_get`, `head_page`,
`crawl_links`, `get_sitemap`, `check_options`, `timing_check`,
`reset_session`, `modify_resource`, `post_form`, custom headers on
`get_page`, a persistent session so logins carry across calls, and six
site-hardening checks — `check_security_headers`, `check_cookie_flags`,
`check_exposed_files`, `check_ssl_certificate`, `check_cors_policy`,
`check_directory_listing`. These report configuration only: header
presence/value, cookie flags (never cookie names or values), whether a
commonly-misconfigured path is publicly reachable (status code only,
content never fetched or stored), TLS certificate metadata (handshake-
level, no content fetched), CORS response headers, and directory-listing
detection via a text pattern. They cannot find or store credentials by
design — that capability was explicitly requested and declined, since a
tool that harvests secrets isn't safe on any domain, including your own.

**Not in the toggle panel, and not configurable from `.env` at all:**

- The single-domain lock — only `TARGET_BASE_URL`'s exact hostname is ever reachable
- The private/loopback/link-local/reserved IP block (this is what stops the domain lock from being routed around via DNS or a redirect to something like `169.254.169.254`)
- The `file://` / `javascript:` / `data:` scheme block
- The system prompt's refusal of credential harvesting, auth bypass, and exploitation techniques

These aren't withheld arbitrarily — they're what makes "only my domain" a
real guarantee instead of a suggestion. A confirmation prompt or a rate
limit is friction you can trade away on your own site; a private-IP or
domain check is the actual boundary, and a tool that lets you switch that
off stops being safe for anyone, including you.

If `ENABLE_CONFIRMATION_PROMPT=false`, mistakes in a task description can
execute against your live site with no pause to catch them — `logs/agent.log`
is your only after-the-fact record, so check it if something unexpected happens.

## 12b. Security limitations — read this

This project is intentionally narrow in scope. It is **not**:

- A penetration-testing tool
- A vulnerability scanner
- Capable of authentication bypass, credential harvesting, session/cookie
  theft, exploitation, arbitrary command execution, port scanning, or
  DDoS — none of these are implemented, and the system prompt explicitly
  instructs the model to decline such requests even if asked

What **is** enforced, in Python (`security.py`), regardless of what the
model says, what the toggle panel is set to, or what the LLM provider is:

- Only `TARGET_BASE_URL`'s exact hostname may be contacted
- DNS resolution is checked — hostnames that resolve to private, loopback,
  link-local, reserved, or multicast IPs are rejected (this also blocks
  cloud metadata endpoints like `169.254.169.254`)
- `file://`, `javascript:`, and `data:` URLs are rejected outright
- Every redirect's `Location` header is validated against the same checks
  before it is ever followed — whether followed automatically
  (`ENABLE_REDIRECT_CONFIRMATION=false`) or reported back for a fresh tool
  call (`ENABLE_REDIRECT_CONFIRMATION=true`), a redirect leaving the domain
  or resolving to a private IP is always rejected, not just deferred
- Path-based host smuggling (e.g. a "path" of `//evil.com` or
  `http://evil.com`) is rejected
- POST requests always require interactive terminal confirmation
- Request timeout, response size, POST body size, tool-call count, and
  request rate are all capped (see `config.py`)

**Residual limitations to be aware of:**
- DNS resolution happens at validation time; in a true TOCTOU attack a
  hostname could theoretically re-resolve between check and connect
  (DNS rebinding). This is a known hard problem for any URL-fetching tool;
  for a research/testing tool against your own site this risk is low, but
  don't point this at anything you don't trust.
- The tool only recognizes JSON POST bodies — it's deliberately not a
  general-purpose HTTP client.
- Free-tier LLM APIs have their own rate limits and quotas independent of
  this project's own limits.

## 13. Swapping the LLM provider later

All provider-specific logic lives in `llm_provider.py` behind one function:

```python
llm_provider.chat(messages) -> {"text": ..., "tool_calls": [...]}
```

`agent.py` and `tools.py` never reference a specific provider. To add a new
one:

1. Write a `_chat_<provider>(messages)` function in `llm_provider.py` that
   sends `tools.TOOL_DEFINITIONS` in whatever schema that provider expects,
   and normalizes its response into `{"text": ..., "tool_calls": [{"id", "name", "arguments"}]}`.
2. Add a branch for it in `chat()`.
3. Set `LLM_PROVIDER=<your-provider>` in `.env`.

Switching between the three built-in providers (Gemini, OpenRouter, Grok)
requires no code changes — just edit `.env`.


# Controlled Web AI Agent

An educational LLM agent that inspects websites using tool-calling. The
model decides which HTTP requests to make; a Python layer executes them.

---

## ⚠️ CRITICAL WARNING — READ THIS FIRST

**SECURITY IS DISABLED IN THIS REPOSITORY.**

The `security.py` file in this build has **all enforcement turned off**.
This is not a joke, not an exaggeration, and not a "soft" disable. Every
single safety check that existed in the original design has been removed
or commented out. Here is exactly what is disabled:

| # | Check | Status | What it means |
|---|---|---|---|
| 1 | Single-domain lock | **DISABLED** | The agent can request any `http://` or `https://` URL on the internet. It is not restricted to your target site. |
| 2 | Private / internal IP block | **DISABLED** | The agent can reach `localhost`, `127.0.0.1`, `192.168.x.x`, `10.x.x.x`, and cloud metadata endpoints like `169.254.169.254` (AWS/GCP/Azure credentials live there). |
| 3 | `file://` / `javascript:` / `data:` scheme block | **DISABLED** | The check is gone. In practice `requests` still cannot fetch these schemes, but the check itself no longer exists. |
| 4 | Path-smuggling check | **DISABLED** | A path like `//evil.com` or `http://evil.com` is no longer rejected. It will be concatenated onto the base URL and sent as-is. |
| 5 | Redirect target validation | **DISABLED** | Redirects are followed automatically wherever they lead, including off-domain and private-IP targets. |
| 6 | POST / PUT / PATCH / DELETE confirmation | **DISABLED** | State-changing requests execute immediately. There is no `y/N` prompt. |

**What this means in plain language:**

- If you run this against `any website`, the agent is not actually
  limited to `website`. It can be tricked (by a prompt injection
  in a website, for example) into fetching `http://169.254.169.254/`
  or `http://localhost:8080/` or `http://attacker.com/`.
- If the agent decides to send `DELETE /api/posts/123`, it will do so
  immediately. There is no pause. There is no confirmation.
- If your site has user-generated content (comments, posts, profile
  bios), a malicious user could embed instructions in that content, and
  the LLM might follow them.

**You must only run this against websites you own or have explicit
written permission to test.** Using it against anything else may violate
computer-misuse laws (see [Legal](#legal) section below).

**Recommended safety practices while using this build:**

1. Run it inside an isolated VM or container, not on your main machine.
2. Do not have cloud credentials, SSH keys, or production secrets
   accessible from that environment.
3. Set `ENABLE_CONFIRMATION_PROMPT=true` in `.env` — even though the
   confirmation logic is disabled in `tools.py`, this documents intent
   and makes it easier to restore later.
4. Review `logs/agent.log` after every single task.
5. Do not leave the agent running unattended.
6. When you are finished, restore the original `security.py` and
   `tools.py` from a safe commit.

---

## Table of Contents

1. [What this is](#1-what-this-is)
2. [What this is not](#2-what-this-is-not)
3. [How the agent works](#3-how-the-agent-works)
4. [System requirements](#4-system-requirements)
5. [Step-by-step installation](#5-step-by-step-installation)
6. [Getting a free LLM API key](#6-getting-a-free-llm-api-key)
7. [Configuring `.env` — every setting explained](#7-configuring-env--every-setting-explained)
8. [Running the agent](#8-running-the-agent)
9. [Understanding the startup banner](#9-understanding-the-startup-banner)
10. [Running your first task](#10-running-your-first-task)
11. [All 17 tools explained](#11-all-17-tools-explained)
12. [Example tasks you can try](#12-example-tasks-you-can-try)
13. [Reading and understanding logs](#13-reading-and-understanding-logs)
14. [Troubleshooting common errors](#14-troubleshooting-common-errors)
15. [Restoring the safe build](#15-restoring-the-safe-build)
16. [Legal](#16-legal)
17. [License](#17-license)

---

## 1. What this is

**Controlled Web AI Agent** is an educational framework for studying how
a Large Language Model (LLM) behaves when given the ability to make real
HTTP requests through a tool-calling loop.

In simple terms:

- You give the agent a task in plain English (e.g. *"check the security
  headers on my homepage"*).
- The agent asks the LLM: *"what should I do first?"*
- The LLM responds with a tool call in structured JSON (e.g.
  `{"name": "check_security_headers", "arguments": {"path": "/"}}`).
- Python executes that tool, captures the result, and sends it back to
  the LLM.
- The LLM reasons about the result and either asks for another tool call
  or gives a final answer.
- This repeats until the LLM stops requesting tools or the
  `MAX_TOOL_CALLS_PER_TASK` limit is reached.

The LLM never touches the network directly. It only ever *requests* tool
calls. Python is the layer that actually sends requests over the wire.

## 2. What this is not

- **Not a penetration-testing tool.** It does not exploit anything.
- **Not a vulnerability scanner.** It reports configuration status only.
- **Not safe to run against third-party sites.** The domain lock is
  disabled in this build.
- **Not a general-purpose HTTP client.** It has a fixed set of 17 tools.
- **Not production-ready.** This is an educational project.

## 3. How the agent works

The agent loop is:

```
   ┌─────────────────────────────────────────┐
   │  1. User types a task in the terminal   │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  2. Task is sent to the LLM (Gemini,    │
   │     OpenRouter, or Grok)                │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  3. LLM responds with either:           │
   │     a) a final text answer, OR          │
   │     b) one or more tool calls (JSON)    │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  4. Python validates the tool call      │
   │     (in this build, validation is       │
   │     mostly disabled — see warning)      │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  5. Python executes the tool            │
   │     (sends the actual HTTP request)     │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  6. Result is logged and sent back to   │
   │     the LLM as a tool message           │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  7. LLM reasons about the result and    │
   │     either calls another tool or gives  │
   │     a final answer                      │
   └────────────────────┬────────────────────┘
                        ▼
   ┌─────────────────────────────────────────┐
   │  8. Loop repeats until final answer or  │
   │     MAX_TOOL_CALLS_PER_TASK reached     │
   └─────────────────────────────────────────┘
```

## 4. System requirements

- **Python 3.8 or newer** ([python.org/downloads](https://www.python.org/downloads/))
- **pip** (comes with Python)
- **Internet connection** (for LLM API and for the site you are testing)
- **A free LLM API key** (see [section 6](#6-getting-a-free-llm-api-key))
- **A terminal / command prompt** (Terminal on Mac/Linux, Command Prompt
  or PowerShell on Windows)

Optional but recommended:

- **Git** ([git-scm.com](https://git-scm.com/)) — to clone the repo and
  restore files later
- **A text editor** (VS Code, Notepad++, Sublime Text, or even Notepad)
- **An isolated VM or Docker container** — see the warning at the top

## 5. Step-by-step installation

### Step 5.1 — Open your terminal

- **Windows:** Press `Win + R`, type `cmd`, press Enter.
- **Mac:** Press `Cmd + Space`, type `Terminal`, press Enter.
- **Linux:** Press `Ctrl + Alt + T`, or find "Terminal" in your app menu.

### Step 5.2 — Download the project

If you have Git:

```bash
git clone https://github.com/your-username/ai-agent.git
cd ai-agent
```

If you do not have Git, download the ZIP from the GitHub page (green
"Code" button → "Download ZIP"), unzip it, then `cd` into the folder:

```bash
cd path/to/ai-agent
```

### Step 5.3 — Create a virtual environment

A virtual environment keeps this project's dependencies separate from
your system Python.

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

**Mac / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
```

You should now see `(venv)` at the start of your terminal prompt.

### Step 5.4 — Install dependencies

```bash
pip install -r requirements.txt
```

This installs two packages: `requests` (for HTTP) and `python-dotenv`
(for loading `.env` files).

### Step 5.5 — Create your `.env` file

Copy the example:

**Windows:**

```bash
copy .env.example .env
```

**Mac / Linux:**

```bash
cp .env.example .env
```

Now open `.env` in a text editor and fill in your API key (see next
section).

### Step 5.6 — Verify the installation

```bash
python agent.py
```

If everything is set up correctly, you will see the startup banner. If
you see an error, check the [Troubleshooting](#14-troubleshooting-common-errors)
section.

## 6. Getting a free LLM API key

You only need **one** of these. The default is Gemini.

### Option A — Google Gemini (recommended, easiest)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
2. Sign in with a Google account.
3. Click **"Create API key"**.
4. Copy the key — it looks like `AIzaSy...` (a long string).
5. Paste it into `.env`:

```ini
LLM_PROVIDER=gemini
MODEL=gemini-1.5-flash
GEMINI_API_KEY=AIzaSy...your-key-here...
```

Free tier limits as of this writing: roughly 15 requests per minute and
1500 requests per day on `gemini-1.5-flash`. These limits change; check
Google's documentation for current numbers.

### Option B — OpenRouter

1. Go to [openrouter.ai/keys](https://openrouter.ai/keys).
2. Sign up (Google or GitHub login works).
3. Click **"Create Key"**, copy it.
4. Browse free models at [openrouter.ai/models?max_price=0](https://openrouter.ai/models?max_price=0).
5. Paste into `.env`:

```ini
LLM_PROVIDER=openrouter
MODEL=meta-llama/llama-3.1-8b-instruct:free
OPENROUTER_API_KEY=sk-or-v1-...your-key-here...
```

### Option C — xAI Grok

1. Go to [console.x.ai](https://console.x.ai).
2. Sign up. (Pricing and free-credit terms change; verify on their site.)
3. Create an API key.
4. Paste into `.env`:

```ini
LLM_PROVIDER=grok
MODEL=grok-beta
GROK_API_KEY=xai-...your-key-here...
```

## 7. Configuring `.env` — every setting explained

Here is every setting in `.env`, what it does, and what value you should
use.

### Provider settings

| Setting | What it does | Recommended value |
|---|---|---|
| `LLM_PROVIDER` | Which LLM service to use | `gemini` |
| `MODEL` | Which model to use | `gemini-1.5-flash` |
| `GEMINI_API_KEY` | Your Gemini API key | Paste your key |
| `OPENROUTER_API_KEY` | Your OpenRouter key (if using OpenRouter) | Leave blank if using Gemini |
| `GROK_API_KEY` | Your xAI key (if using Grok) | Leave blank if using Gemini |

### Target settings

| Setting | What it does | Recommended value |
|---|---|---|
| `TARGET_BASE_URL` | The website the agent is told to focus on | `https://your-website.com` |

**Important:** In this build, the domain lock is disabled. The agent is
told to focus on `TARGET_BASE_URL`, but it is not actually restricted to
it. If the LLM hallucinates or is injected with instructions, it can
request any URL.

### Safety limit settings

| Setting | What it does | Default | Effect if you lower it |
|---|---|---|---|
| `REQUEST_TIMEOUT_SECONDS` | Maximum wait time for one HTTP request | `30` | Requests time out sooner |
| `MAX_RESPONSE_BYTES` | Maximum size of a response body that is returned to the LLM | `5000000` (5 MB) | Larger responses are truncated |
| `MAX_TOOL_CALLS_PER_TASK` | Maximum tool calls per task | `100` | Agent stops earlier |
| `MAX_POST_BODY_BYTES` | Maximum size of a POST body | `5000000` (5 MB) | Larger POSTs are rejected |
| `MIN_SECONDS_BETWEEN_REQUESTS` | Delay between requests (only if rate limit is on) | `0` | N/A when rate limit is off |
| `LOG_FILE_PATH` | Where the log file is written | `logs/agent.log` | Change to any path |

### Toggle settings

| Setting | What it does | Recommended |
|---|---|---|
| `ENABLE_CONFIRMATION_PROMPT` | If `true`, POST/PUT/PATCH/DELETE would wait for `y/N` | `false` (but see warning — the confirmation logic is disabled in `tools.py` regardless) |
| `ENABLE_RATE_LIMIT` | If `true`, waits between requests | `false` |
| `ENABLE_REDIRECT_CONFIRMATION` | If `true`, redirects would be reported instead of followed | `false` |
| `AUTO_REDIRECT_MAX_HOPS` | Maximum redirects to follow | `5` |
| `AUTO_APPROVE_POST_PATHS` | Paths that skip the prompt (when prompt is on) | `/` |

### Complete working `.env` example

```ini
# Provider
LLM_PROVIDER=gemini
MODEL=gemini-1.5-flash
GEMINI_API_KEY=AIzaSy...your-key-here...

# Target
TARGET_BASE_URL=https://your-website.com

# Limits
REQUEST_TIMEOUT_SECONDS=30
MAX_RESPONSE_BYTES=5000000
MAX_TOOL_CALLS_PER_TASK=100
MAX_POST_BODY_BYTES=5000000
MIN_SECONDS_BETWEEN_REQUESTS=0
LOG_FILE_PATH=logs/agent.log

# Toggles
ENABLE_CONFIRMATION_PROMPT=false
ENABLE_RATE_LIMIT=false
ENABLE_REDIRECT_CONFIRMATION=false
AUTO_REDIRECT_MAX_HOPS=5
AUTO_APPROVE_POST_PATHS=/
```

**Never commit `.env` to Git.** Add `.env` and `logs/` to `.gitignore`.

## 8. Running the agent

### Basic run

```bash
python agent.py
```

### What you will see

The startup banner:

```
====================================================
 Controlled Web AI Agent  —  TESTING MODE
====================================================

Provider: gemini  Model: gemini-1.5-flash
Target: https://your-website.com

-- Toggles (set in .env) -----------------------
  Confirmation prompt (POST/PUT/PATCH/DELETE): OFF
  Rate limiting:                                OFF
  Redirect confirmation (off = auto-follow):    OFF

-- Enforcement status (testing mode) -----------
  Single-domain lock:                           OFF
  Private/metadata IP block:                    OFF
  file:// / javascript: / data: scheme block:   OFF
  Redirect target validation:                   OFF
  POST/PUT/PATCH/DELETE confirmation prompt:    OFF
  Sensitive-data redaction in logs:             ON
  POST body size cap:                           ON
  Host / Content-Length header block:           ON
----------------------------------------------------
  ⚠️  TESTING MODE — not safe for production use.
     Check logs/agent.log after every task.
----------------------------------------------------

Enter your task (or 'quit' to exit):
>
```

### Exiting

Type `quit` or `exit` and press Enter. Or press `Ctrl + C`.

## 9. Understanding the startup banner

Every line in the banner tells you something important. Here is what each
one means:

**`Provider: gemini  Model: gemini-1.5-flash`** — which LLM you are
using.

**`Target: https://your-website.com`** — the site you configured. The
agent is told to focus on this, but with the domain lock off, it is not
enforced.

**`Confirmation prompt: OFF`** — state-changing requests execute without
asking you. To change this, set `ENABLE_CONFIRMATION_PROMPT=true` in
`.env` (but note that even then, the confirmation logic in `tools.py` is
currently disabled in this build).

**`Rate limiting: OFF`** — no delay between requests. If you are testing
a fragile site, set `ENABLE_RATE_LIMIT=true` in `.env`.

**`Redirect confirmation: OFF`** — redirects are followed automatically.

**`Single-domain lock: OFF`** — the agent is not restricted to your
target. This is the single most important line in the banner.

**`Private/metadata IP block: OFF`** — the agent can reach `localhost`,
private networks, and cloud metadata endpoints.

**`file:// / javascript: / data: scheme block: OFF`** — the check is
gone (though `requests` still cannot fetch these schemes).

**`Redirect target validation: OFF`** — redirects are not checked before
being followed.

**`POST/PUT/PATCH/DELETE confirmation prompt: OFF`** — no `y/N` prompt
before state-changing requests.

**`Sensitive-data redaction in logs: ON`** — `Authorization`, `Cookie`,
`Set-Cookie` headers and JSON keys like `password`, `token`, `api_key`
are replaced with `***REDACTED***` before being written to the log. This
is still active and you should keep it.

**`POST body size cap: ON`** — POST bodies over `MAX_POST_BODY_BYTES`
are rejected.

**`Host / Content-Length header block: ON`** — the agent cannot set
these two headers, which prevents request smuggling on misconfigured
proxies.

## 10. Running your first task

Start with a read-only task so nothing can be modified.

At the `>` prompt, type:

```
Check the security headers on my homepage
```

Press Enter.

**What you will see:**

```
Agent:
I'll fetch your homepage and check the security headers.

TOOL: CHECK_SECURITY_HEADERS /
STATUS: 200

Agent:
Here is what I found on https://your-website.com/:

✅ Strict-Transport-Security: present (max-age=63072000)
❌ Content-Security-Policy: NOT present
✅ X-Frame-Options: present (SAMEORIGIN)
✅ X-Content-Type-Options: present (nosniff)
❌ Referrer-Policy: NOT present
❌ Permissions-Policy: NOT present

FINAL ANSWER:
Your site has HSTS, X-Frame-Options, and X-Content-Type-Options set.
Missing: CSP, Referrer-Policy, Permissions-Policy.
```

The exact output depends on what your site actually returns.

To stop, type:

```
quit
```

## 11. All 17 tools explained

The agent has 17 tools. Each one does exactly one thing. Here they are,
grouped by purpose.

### Group 1 — Read-only inspection (8 tools)

**`get_page`** — Sends an HTTP GET to a path (e.g. `/about`). Returns
status code, response headers, and body. You can also pass custom
headers (except `Host` and `Content-Length`).

**`batch_get`** — Sends multiple GETs in one tool call. Useful when the
LLM wants to inspect several pages at once. Each path goes through the
same per-request handling as `get_page`.

**`head_page`** — Sends an HTTP HEAD request. Returns status and headers
only, no body. Useful for checking if a page exists, its content type, or
its caching headers without downloading the full page.

**`crawl_links`** — Fetches a page and extracts every `<a href="...">`
link from the HTML. Same-domain links are listed separately from external
ones. The tool itself only fetches the one page — it does not follow any
links.

**`get_sitemap`** — Fetches `/robots.txt` and `/sitemap.xml` in one call.
Useful for understanding a site's structure.

**`check_options`** — Sends an HTTP OPTIONS request. Returns the `Allow`
header, which lists which methods (GET, POST, PUT, etc.) the path
supports.

**`timing_check`** — Sends the same GET request up to 10 times and
reports min / max / average response time. Useful for spotting slow
endpoints.

**`reset_session`** — Clears the agent's local cookie jar. No network
call is made.

### Group 2 — Security / configuration checks (6 tools)

**`check_security_headers`** — Fetches a page and reports whether these
headers are present: `Strict-Transport-Security`, `Content-Security-Policy`,
`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`,
`Permissions-Policy`. Reports the value of each.

**`check_cookie_flags`** — Fetches a page and reports, for each
`Set-Cookie` header, whether the cookie has the `Secure`, `HttpOnly`, and
`SameSite` flags set. **Never reports cookie names or values** — flags
only.

**`check_exposed_files`** — Sends HEAD requests to a list of commonly
misconfigured paths (`/.env`, `/.git/config`, `/backup.sql`,
`/wp-config.php`, etc.) and reports only the HTTP status. **Never fetches
or stores the contents** of any file. A `200` means something is publicly
reachable at that path.

**`check_ssl_certificate`** — Opens a TLS connection to port 443 and
reports the certificate issuer, subject, validity dates, days until
expiry, and negotiated TLS version. Handshake-level check only — no page
content is fetched.

**`check_cors_policy`** — Sends a GET with a foreign `Origin` header and
reports the server's CORS response headers. Flags a wildcard
`Access-Control-Allow-Origin: *` combined with
`Access-Control-Allow-Credentials: true`, which is a known
misconfiguration.

**`check_directory_listing`** — Sends GET requests to a list of common
directories (`/admin/`, `/uploads/`, `/backup/`, etc.) and flags any that
look like they have directory listing enabled (index-of-style pages).
Uses a simple text-pattern heuristic. Verify flagged paths manually.

### Group 3 — State-changing (3 tools)

**`post_json`** — Sends an HTTP POST with a JSON body. In safe builds,
this would require human confirmation. **In this build, it does not.**

**`post_form`** — Sends an HTTP POST with an
`application/x-www-form-urlencoded` body (the format traditional HTML
forms use). **No confirmation in this build.**

**`modify_resource`** — Sends PUT, PATCH, or DELETE to a path.
**No confirmation in this build.** A DELETE against real data will
actually delete it.

## 12. Example tasks you can try

All of these are read-only (they use Group 1 or Group 2 tools only).
Start with these. Do not attempt any task that uses `post_json`,
`post_form`, or `modify_resource` until you fully understand the risks.

### Task 1 — Basic site inspection

```
Inspect my homepage and tell me what technologies you can detect from the headers.
```

Uses: `get_page`.

### Task 2 — Security headers

```
Check the security headers on my homepage.
```

Uses: `check_security_headers`.

### Task 3 — SSL certificate

```
Check my SSL certificate and tell me when it expires and who issued it.
```

Uses: `check_ssl_certificate`.

### Task 4 — Cookie flags

```
Check the cookie flags on my homepage.
```

Uses: `check_cookie_flags`.

### Task 5 — Site structure

```
Crawl my homepage and list all the same-domain links you find.
```

Uses: `crawl_links`.

### Task 6 — Sitemap

```
Fetch my sitemap and robots.txt and summarize what's in them.
```

Uses: `get_sitemap`.

### Task 7 — Exposed files check

```
Check whether any of my sensitive files like .env or .git/config are publicly accessible.
```

Uses: `check_exposed_files`.

### Task 8 — CORS policy

```
Check my CORS policy and tell me if there's a wildcard origin with credentials issue.
```

Uses: `check_cors_policy`.

### Task 9 — Directory listing

```
Check common directories on my site for directory listing.
```

Uses: `check_directory_listing`.

### Task 10 — Full security audit

```
Do a full security audit: check headers, SSL, cookies, CORS, exposed files, and directory listing. Give me a prioritized list of issues.
```

Uses: all six Group 2 tools in sequence. This will use several LLM
requests, so watch your free-tier quota.

### Task 11 — Performance check

```
Check the response time of my homepage by making 5 requests and telling me the average.
```

Uses: `timing_check`.

### Task 12 — HTTP methods

```
Check which HTTP methods my /api endpoint supports.
```

Uses: `check_options`.

## 13. Reading and understanding logs

Every tool call is written to `logs/agent.log` as a JSON line. The log
directory is created automatically the first time the agent runs.

### Log format

Each line is a complete JSON object:

```json
{"tool": "get_page", "path": "/", "url": "https://your-website.com/", "method": "GET", "status": 200, "timestamp": "2025-01-15T10:30:00.000000+00:00"}
```

### Fields explained

| Field | What it means |
|---|---|
| `tool` | Which tool was called (`get_page`, `post_json`, etc.) |
| `method` | HTTP method used (`GET`, `POST`, `PUT`, `DELETE`) |
| `path` | The path that was requested (`/`, `/about`, `/api/status`) |
| `url` | The full URL that was requested |
| `status` | HTTP status code returned (`200`, `404`, `500`, etc.) |
| `timestamp` | When the request happened (UTC) |
| `request_body` | The body sent (for POST/PUT/PATCH) — sensitive keys are redacted |
| `response_headers` | Response headers received — sensitive headers are redacted |
| `approved` | Whether the human approved the request (`true` / `false` / `null`) |
| `error` | Error message if the request failed |
| `blocked` | `true` if the request was blocked by a security check |
| `reason` | Why it was blocked (if `blocked` is `true`) |

### Example: successful GET

```json
{"tool": "get_page", "path": "/", "url": "https://your-website.com/", "method": "GET", "status": 200, "timestamp": "2025-01-15T10:30:00.000000+00:00"}
```

### Example: POST with redacted body

```json
{"tool": "post_json", "path": "/api/test", "url": "https://your-website.com/api/test", "method": "POST", "status": 200, "request_body": {"message": "hello", "password": "***REDACTED***"}, "approved": true, "timestamp": "2025-01-15T10:30:05.000000+00:00"}
```

Note how `password` was replaced with `***REDACTED***`.

### How to read the log

- **On Mac/Linux:** `tail -f logs/agent.log` (streams new entries live)
- **On Windows (PowerShell):** `Get-Content logs/agent.log -Wait`
- **In any text editor:** open `logs/agent.log` directly

### What to look for

- **Unexpected URLs.** If you see a URL that is not on your target site,
  investigate immediately. This means the agent went somewhere you did
  not intend.
- **Unexpected methods.** A `DELETE` or `PUT` you did not ask for means
  something is wrong.
- **`blocked: true` entries.** These are requests that were blocked. In
  this build, very few things are blocked, so this will be rare.
- **Errors.** `error` fields explain why a request failed. Timeouts,
  connection errors, and HTTP errors all show up here.

## 14. Troubleshooting common errors

### `python: command not found`

Python is not installed, or not on your PATH.

**Fix:** Download Python from [python.org/downloads](https://www.python.org/downloads/).
On Windows, tick **"Add Python to PATH"** during installation.

### `ModuleNotFoundError: No module named 'requests'`

Dependencies are not installed, or you forgot to activate the virtual
environment.

**Fix:**

```bash
# Activate venv first
source venv/bin/activate    # Mac/Linux
venv\Scripts\activate       # Windows

# Then install
pip install -r requirements.txt
```

### `TARGET_BASE_URL is not set`

The `.env` file is missing, or `TARGET_BASE_URL` is empty.

**Fix:**

1. Confirm `.env` exists in the same folder as `agent.py`.
2. Open it and check `TARGET_BASE_URL=https://your-website.com` is set.
3. Confirm the file is named exactly `.env`, not `.env.txt`.

### `Missing GEMINI_API_KEY in .env`

The API key is empty.

**Fix:** Open `.env`, find `GEMINI_API_KEY=`, and paste your key after
the `=`.

### `LLM request to gemini failed: 400`

The API key is wrong or malformed.

**Fix:** Go back to [aistudio.google.com/apikey](https://aistudio.google.com/apikey),
create a new key, and paste it into `.env`.

### `LLM request to gemini failed: 429`

You have hit the free-tier rate limit.

**Fix:** Wait a minute and try again. If it persists, you have used your
daily quota. Try a different provider (OpenRouter) or wait until the
quota resets.

### `Connection error contacting your-website.com`

Your site is unreachable, or you have no internet.

**Fix:** Open the site in a browser. If it loads there, try again in the
agent. If it does not, the site is down.

### `Request timed out after 30s`

The site is slow, or the request is hanging.

**Fix:** Increase `REQUEST_TIMEOUT_SECONDS` in `.env` to `60`, or check
whether the site is under heavy load.

### Agent stops with `[Agent stopped: reached MAX_TOOL_CALLS_PER_TASK limit.]`

The LLM used up all its tool-call budget for this task.

**Fix:** Increase `MAX_TOOL_CALLS_PER_TASK` in `.env`, or break the task
into smaller tasks.

### Agent keeps asking the same tool call

The LLM is confused or stuck in a loop.

**Fix:** Press `Ctrl + C` to stop. Rephrase your task in clearer terms.

### Nothing happens when I type a task

The agent may be waiting for the LLM to respond, which can take a few
seconds. If nothing happens for over a minute, press `Ctrl + C` and
check your internet connection.

## 15. Restoring the safe build

If you want to restore the original (safe) behavior — domain lock, IP
block, scheme block, redirect validation, and confirmation prompts — you
need to restore `security.py` and `tools.py` from a version before they
were modified.

### If you cloned with Git

```bash
git log --oneline -- security.py tools.py
```

Find the last safe commit, then:

```bash
git checkout <commit-hash> -- security.py tools.py
```

### If you do not have Git

You need to obtain the original `security.py` and `tools.py` files from
the project maintainer or from a release that shipped in safe mode.

### After restoring

Run `python agent.py` again. The banner should show:

```
-- Enforcement status -----------
  Single-domain lock:                           ON
  Private/metadata IP block:                    ON
  ...
```

If it still shows `OFF`, the files were not replaced correctly.

## 16. Legal

**Only use this software against systems you own or have explicit
written permission to test.**

Unauthorized access, modification, or disruption of computer systems is
illegal in most jurisdictions, including:

- **Pakistan** — Prevention of Electronic Crimes Act (PECA) 2016,
  sections 3 (unauthorized access), 4 (unauthorized copying or
  transmission of data), 5 (interference with information systems)
- **United States** — Computer Fraud and Abuse Act, 18 U.S.C. § 1030
- **United Kingdom** — Computer Misuse Act 1990
- **European Union** — Directive 2013/40/EU and applicable national
  implementations (e.g. Germany's StGB § 202a–c, France's Code pénal
  Art. 323-1 et seq.)

**The authors and contributors:**

- Do not condone or support unauthorized access to computer systems.
- Are not responsible for any misuse, damage, or legal consequences.
- Provide this software **"as is"**, without warranty of any kind.

If you are unsure whether your intended use is authorized, do not
proceed. Seek legal advice.

## 17. License

MIT. See the `LICENSE` file for the full text.

**Additional notice:** The MIT License applies to the source code. It
does not grant permission to use this software against systems you do
not own or are not authorized to test.

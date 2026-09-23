"""
llm_provider.py — Thin, swappable adapter over multiple LLM providers.

Supported providers (set LLM_PROVIDER in .env):
  - "gemini"     : Google Gemini API (has a free tier). Uses google-generativeai
                    if installed, otherwise raw REST via `requests`.
  - "openrouter" : OpenRouter.ai — gives access to many models, several
                    tagged ":free". Uses OpenAI-compatible REST API.
  - "grok"       : xAI Grok API (OpenAI-compatible REST API).

All three are exposed through one function: `chat(messages, tools)` which
returns a normalized response:

    {
        "text": str | None,          # final natural-language text, if any
        "tool_calls": [              # list of requested tool calls, if any
            {"id": str, "name": str, "arguments": dict}
        ],
    }

This normalization is what makes it easy to swap providers later — agent.py
never needs to know which provider it's talking to.
"""

import json

import requests

import config

# ---------------------------------------------------------------------------
# OpenAI-compatible providers (OpenRouter, Grok) share almost identical
# request/response shapes, so we share one implementation for both.
# ---------------------------------------------------------------------------

_OPENAI_COMPATIBLE_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "grok": "https://api.x.ai/v1/chat/completions",
}


def _openai_compatible_tools_schema():
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in __import__("tools").TOOL_DEFINITIONS
    ]


def _chat_openai_compatible(provider: str, messages: list) -> dict:
    endpoint = _OPENAI_COMPATIBLE_ENDPOINTS[provider]
    api_key = config.OPENROUTER_API_KEY if provider == "openrouter" else config.GROK_API_KEY

    if not api_key:
        raise RuntimeError(
            f"Missing API key for provider '{provider}'. Set "
            f"{'OPENROUTER_API_KEY' if provider == 'openrouter' else 'GROK_API_KEY'} in .env."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # OpenRouter appreciates (optional) attribution headers; harmless if unused.
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://localhost"
        headers["X-Title"] = "controlled-web-agent"

    payload = {
        "model": config.MODEL,
        "messages": messages,
        "tools": _openai_compatible_tools_schema(),
        "tool_choice": "auto",
    }

    try:
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        detail = getattr(e.response, "text", "") if getattr(e, "response", None) else ""
        raise RuntimeError(f"LLM request to {provider} failed: {e} {detail}")

    data = resp.json()
    choice = data["choices"][0]["message"]

    tool_calls = []
    for tc in choice.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({"id": tc["id"], "name": tc["function"]["name"], "arguments": args})

    return {"text": choice.get("content"), "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# Gemini (Google) — REST API, OpenAI-incompatible shape, normalized here.
# ---------------------------------------------------------------------------

def _gemini_tools_schema():
    import tools as tools_module

    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                }
                for t in tools_module.TOOL_DEFINITIONS
            ]
        }
    ]


def _messages_to_gemini_contents(messages: list) -> tuple:
    """
    Convert our normalized message history (OpenAI-style roles) into
    Gemini's `contents` format. Returns (system_instruction, contents).
    """
    system_instruction = None
    contents = []

    for msg in messages:
        role = msg["role"]

        if role == "system":
            system_instruction = msg["content"]
            continue

        if role == "user":
            contents.append({"role": "user", "parts": [{"text": msg["content"]}]})

        elif role == "assistant":
            parts = []
            if msg.get("content"):
                parts.append({"text": msg["content"]})
            for tc in msg.get("tool_calls", []):
                parts.append({
                    "function_call": {"name": tc["name"], "args": tc["arguments"]}
                })
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            contents.append({
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": msg["name"],
                        "response": {"result": msg["content"]},
                    }
                }],
            })

    return system_instruction, contents


def _chat_gemini(messages: list) -> dict:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("Missing GEMINI_API_KEY in .env.")

    system_instruction, contents = _messages_to_gemini_contents(messages)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.MODEL}:generateContent?key={config.GEMINI_API_KEY}"
    )

    payload = {
        "contents": contents,
        "tools": _gemini_tools_schema(),
    }
    if system_instruction:
        payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

    try:
        resp = requests.post(url, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        detail = getattr(e.response, "text", "") if getattr(e, "response", None) else ""
        raise RuntimeError(f"LLM request to gemini failed: {e} {detail}")

    data = resp.json()

    if "candidates" not in data or not data["candidates"]:
        block_reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
        raise RuntimeError(f"Gemini returned no candidates (reason: {block_reason}).")

    candidate = data["candidates"][0]
    parts = candidate.get("content", {}).get("parts", [])

    text_parts = []
    tool_calls = []
    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": f"gemini-call-{i}",
                "name": fc["name"],
                "arguments": fc.get("args", {}),
            })

    return {"text": "\n".join(text_parts) if text_parts else None, "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def chat(messages: list) -> dict:
    """
    Send the conversation to the configured provider and return a
    normalized {"text": ..., "tool_calls": [...]} response.
    """
    provider = config.LLM_PROVIDER

    if provider == "gemini":
        return _chat_gemini(messages)
    elif provider in ("openrouter", "grok"):
        return _chat_openai_compatible(provider, messages)
    else:
        raise RuntimeError(
            f"Unknown LLM_PROVIDER '{provider}'. Use 'gemini', 'openrouter', or 'grok'."
        )

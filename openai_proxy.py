#!/usr/bin/env python3
"""
OpenAI API reverse proxy: forwards /v1/* to the OpenAI API with optional streaming.

Environment
-----------
OPENAI_API_KEY (required)
    Bearer token sent to api.openai.com.

OPENAI_BASE_URL (optional)        Default: https://api.openai.com
ALLOWED_MODELS                    Hard-coded allow-list: gpt-5.3-codex, gpt-5.4-mini
                                  Requests with any other model are rejected with HTTP 400.
LISTEN_HOST (optional)            Default: 0.0.0.0
LISTEN_PORT (optional)            Default: 1234

PROXY_BEARER_TOKEN (optional)
    If set, client must send ``Authorization: Bearer <this value>``.

PROXY_TOKEN_MAP (optional)
    Comma-separated ``client_token=sk-openai-key`` pairs. Overrides PROXY_BEARER_TOKEN.

TOKEN_DB_PATH (optional)          Default: /tmp/token_usage.db
IP_INPUT_TOKEN_LIMIT (optional)   Default: 100000
IP_OUTPUT_TOKEN_LIMIT (optional)  Default: 100000

Admin endpoints
---------------
GET    /admin/usage              — per-IP token usage + configured limits
DELETE /admin/usage/<ip>         — reset counters for one IP
POST   /admin/usage/reset        — JSON body: ``{"ip": "<addr>"}`` or ``{"all": true}`` to clear all

Run: pip install -r openai_proxy_requirements.txt && python openai_proxy.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from typing import Iterator

import requests
from flask import Flask, Response, abort, request, stream_with_context

app = Flask(__name__)

OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
UPSTREAM_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
PROXY_BEARER_TOKEN = os.environ.get("PROXY_BEARER_TOKEN", "").strip()
PROXY_TOKEN_MAP_RAW = os.environ.get("PROXY_TOKEN_MAP", "").strip()

TOKEN_DB_PATH = os.environ.get("TOKEN_DB_PATH", "/tmp/token_usage.db")
IP_INPUT_TOKEN_LIMIT = int(os.environ.get("IP_INPUT_TOKEN_LIMIT", "100000"))
IP_OUTPUT_TOKEN_LIMIT = int(os.environ.get("IP_OUTPUT_TOKEN_LIMIT", "100000"))

HOP_HEADERS = frozenset(
    {
        "content-type",
        "accept",
        "accept-encoding",
        "openai-organization",
        "openai-project",
        "user-agent",
    }
)

ALLOWED_MODELS: frozenset[str] = frozenset({"gpt-5.3-codex", "gpt-5.4-mini"})

# ── SQLite token store ────────────────────────────────────────────────────────

_db_lock = threading.Lock()


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(TOKEN_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _db_lock, _get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ip_token_usage (
                ip            TEXT PRIMARY KEY,
                input_tokens  INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()


def _get_usage(ip: str) -> tuple[int, int]:
    with _db_lock, _get_db() as conn:
        row = conn.execute(
            "SELECT input_tokens, output_tokens FROM ip_token_usage WHERE ip = ?",
            (ip,),
        ).fetchone()
    return (row["input_tokens"], row["output_tokens"]) if row else (0, 0)


def _record_usage(ip: str, input_delta: int, output_delta: int) -> None:
    if input_delta == 0 and output_delta == 0:
        return
    with _db_lock, _get_db() as conn:
        conn.execute(
            """
            INSERT INTO ip_token_usage (ip, input_tokens, output_tokens, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(ip) DO UPDATE SET
                input_tokens  = input_tokens  + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                updated_at    = excluded.updated_at
            """,
            (ip, max(0, input_delta), max(0, output_delta)),
        )
        conn.commit()


def _reset_usage(ip: str) -> bool:
    with _db_lock, _get_db() as conn:
        cur = conn.execute("DELETE FROM ip_token_usage WHERE ip = ?", (ip,))
        conn.commit()
    return cur.rowcount > 0


def _reset_all_usage() -> int:
    with _db_lock, _get_db() as conn:
        cur = conn.execute("DELETE FROM ip_token_usage")
        conn.commit()
    return cur.rowcount


def _all_usage() -> list[dict]:
    with _db_lock, _get_db() as conn:
        rows = conn.execute(
            "SELECT ip, input_tokens, output_tokens, updated_at "
            "FROM ip_token_usage ORDER BY ip"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Auth helpers ──────────────────────────────────────────────────────────────


def _parse_token_map() -> dict[str, str]:
    out: dict[str, str] = {}
    if not PROXY_TOKEN_MAP_RAW:
        return out
    for pair in PROXY_TOKEN_MAP_RAW.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        out[k.strip()] = v.strip()
    return out


_TOKEN_MAP = _parse_token_map()


def _resolve_upstream_auth() -> str:
    if _TOKEN_MAP:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        key = _TOKEN_MAP.get(auth[7:].strip())
        if not key:
            abort(401)
        return f"Bearer {key}"

    if PROXY_BEARER_TOKEN:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        if auth[7:].strip() != PROXY_BEARER_TOKEN:
            abort(401)

    if not UPSTREAM_KEY:
        abort(500, description="OPENAI_API_KEY is not set")
    return f"Bearer {UPSTREAM_KEY}"


def _build_upstream_headers() -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() in HOP_HEADERS}
    headers["Authorization"] = _resolve_upstream_auth()
    return headers


def _client_ip() -> str:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _limit_response(kind: str, limit: int) -> Response:
    return Response(
        json.dumps(
            {
                "error": {
                    "message": f"{kind} token limit ({limit:,}) reached for your IP.",
                    "type": "rate_limit_error",
                    "code": "token_limit_exceeded",
                }
            }
        ),
        status=429,
        content_type="application/json",
    )


# ── Proxy route ───────────────────────────────────────────────────────────────


@app.route("/v1/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy_v1(subpath: str):
    ip = _client_ip()

    used_input, used_output = _get_usage(ip)
    if used_input >= IP_INPUT_TOKEN_LIMIT:
        return _limit_response("Input", IP_INPUT_TOKEN_LIMIT)
    if used_output >= IP_OUTPUT_TOKEN_LIMIT:
        return _limit_response("Output", IP_OUTPUT_TOKEN_LIMIT)

    headers = _build_upstream_headers()

    raw = request.get_data()
    is_streaming = False
    try:
        body = json.loads(raw)
        requested_model = body.get("model", "")
        if requested_model not in ALLOWED_MODELS:
            return Response(
                json.dumps(
                    {
                        "error": {
                            "message": (
                                f"Model '{requested_model}' is not allowed. "
                                f"Allowed models: {sorted(ALLOWED_MODELS)}"
                            ),
                            "type": "invalid_request_error",
                            "code": "model_not_allowed",
                        }
                    }
                ),
                status=400,
                content_type="application/json",
            )
        if body.get("stream"):
            is_streaming = True
            # Inject stream_options for /v1/chat/completions to get exact token counts
            if subpath == "chat/completions":
                body["stream_options"] = {"include_usage": True}
        raw = json.dumps(body).encode()
    except Exception:
        pass  # non-JSON body — forward as-is

    try:
        upstream = requests.request(
            method=request.method,
            url=f"{OPENAI_BASE}/v1/{subpath}",
            headers=headers,
            params=request.args,
            data=raw,
            stream=True,
            timeout=(30, 600),
        )
    except requests.RequestException:
        abort(502, description="Upstream request failed")

    out_headers = [("Cache-Control", "no-cache"), ("X-Accel-Buffering", "no")]
    ct = upstream.headers.get("Content-Type", "")
    if ct:
        out_headers.append(("Content-Type", ct))

    if is_streaming:
        def generate_streaming() -> Iterator[bytes]:
            """
            Stream SSE to client. Buffers across chunk boundaries so large
            events (e.g. response.completed) are never split mid-parse.

            Supported token sources:
              /v1/chat/completions — usage-only chunk: choices=[], usage={prompt_tokens, completion_tokens}
              /v1/responses        — response.completed event: response.usage.{input_tokens, output_tokens}
            """
            line_buf = ""
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    line_buf += chunk.decode("utf-8", errors="ignore")
                    parts = line_buf.split("\n")
                    line_buf = parts[-1]
                    complete_lines = parts[:-1]

                    filtered: list[str] = []
                    for line in complete_lines:
                        stripped = line.strip()
                        drop = False
                        if stripped.startswith("data:") and stripped != "data: [DONE]":
                            try:
                                payload = json.loads(stripped[5:].strip())

                                # /v1/chat/completions: usage-only final chunk
                                usage = payload.get("usage")
                                if usage and payload.get("choices") == []:
                                    _record_usage(
                                        ip,
                                        usage.get("prompt_tokens", 0),
                                        usage.get("completion_tokens", 0),
                                    )
                                    drop = True
                            except Exception:
                                pass
                        if not drop:
                            filtered.append(line + "\n")

                    out = "".join(filtered)
                    if out.strip():
                        yield out.encode("utf-8")

                if line_buf.strip():
                    yield line_buf.encode("utf-8")
            finally:
                upstream.close()

        return Response(
            stream_with_context(generate_streaming()),
            status=upstream.status_code,
            headers=out_headers,
        )

    upstream.close()
    abort(400, description="Non-streaming requests are not supported. Set stream=true.")


# ── Admin endpoints ───────────────────────────────────────────────────────────


@app.route("/admin/usage", methods=["GET"])
def admin_usage():
    return Response(
        json.dumps(
            {
                "limits": {
                    "input_tokens": IP_INPUT_TOKEN_LIMIT,
                    "output_tokens": IP_OUTPUT_TOKEN_LIMIT,
                },
                "usage": _all_usage(),
            },
            indent=2,
        ),
        content_type="application/json",
    )


@app.route("/admin/usage/<path:ip>", methods=["DELETE"])
def admin_reset_usage(ip: str):
    if not _reset_usage(ip):
        return Response(
            json.dumps({"error": f"No record found for IP '{ip}'"}),
            status=404,
            content_type="application/json",
        )
    return Response(json.dumps({"ok": True, "reset": ip}), content_type="application/json")


@app.route("/admin/usage/reset", methods=["POST"])
def admin_reset_usage_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return Response(
            json.dumps({"error": "Expected JSON object body"}),
            status=400,
            content_type="application/json",
        )
    if payload.get("all") is True:
        cleared = _reset_all_usage()
        return Response(
            json.dumps({"ok": True, "cleared_rows": cleared}),
            content_type="application/json",
        )
    ip = payload.get("ip")
    if not ip or not isinstance(ip, str):
        return Response(
            json.dumps(
                {
                    "error": 'Send {"ip": "<address>"} or {"all": true}',
                }
            ),
            status=400,
            content_type="application/json",
        )
    ip = ip.strip()
    if not _reset_usage(ip):
        return Response(
            json.dumps({"error": f"No record found for IP '{ip}'"}),
            status=404,
            content_type="application/json",
        )
    return Response(json.dumps({"ok": True, "reset": ip}), content_type="application/json")


@app.route("/healthz")
def healthz():
    return {"ok": True}, 200


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    host = os.environ.get("LISTEN_HOST", "0.0.0.0")
    port = int(os.environ.get("LISTEN_PORT", "1234"))
    if not UPSTREAM_KEY and not _TOKEN_MAP:
        print("error: set OPENAI_API_KEY or PROXY_TOKEN_MAP", file=sys.stderr)
        sys.exit(1)
    _init_db()
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()

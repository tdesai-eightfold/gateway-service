#!/usr/bin/env python3
"""
OpenAI API reverse proxy: forwards /v1/* to the OpenAI API with optional streaming.

Environment
-----------
OPENAI_API_KEY (required)
    Bearer token sent to api.openai.com.

OPENAI_BASE_URL (optional)
    Default: https://api.openai.com

LISTEN_HOST (optional)  Default: 0.0.0.0
LISTEN_PORT (optional)  Default: 1234

Optional gate (Idea C)
----------------------
PROXY_BEARER_TOKEN
    If set, the client must send ``Authorization: Bearer <this exact value>``.
    Upstream still uses OPENAI_API_KEY.

PROXY_TOKEN_MAP
    Comma-separated ``client_token=sk-openai-key``. Client Bearer must match
    ``client_token``; the mapped ``sk-...`` is used upstream. If set,
    PROXY_BEARER_TOKEN is ignored.

If neither PROXY_BEARER_TOKEN nor PROXY_TOKEN_MAP is set, no client auth is
required; upstream always uses OPENAI_API_KEY (suitable only on trusted networks).

Run: pip install -r openai_proxy_requirements.txt && python openai_proxy.py
"""

from __future__ import annotations

import os
import sys
from typing import Iterator

import json
import requests
from flask import Flask, Response, abort, request, stream_with_context

app = Flask(__name__)

OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/")
UPSTREAM_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
PROXY_BEARER_TOKEN = os.environ.get("PROXY_BEARER_TOKEN", "").strip()
PROXY_TOKEN_MAP_RAW = os.environ.get("PROXY_TOKEN_MAP", "").strip()

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


def resolve_upstream_authorization() -> str:
    if not UPSTREAM_KEY and not _TOKEN_MAP:
        abort(500, description="OPENAI_API_KEY is not set")

    if _TOKEN_MAP:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        client = auth[7:].strip()
        key = _TOKEN_MAP.get(client)
        if not key:
            abort(401)
        return f"Bearer {key}"

    if PROXY_BEARER_TOKEN:
        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            abort(401)
        client = auth[7:].strip()
        if client != PROXY_BEARER_TOKEN:
            abort(401)
        if not UPSTREAM_KEY:
            abort(500, description="OPENAI_API_KEY is not set")
        return f"Bearer {UPSTREAM_KEY}"

    if not UPSTREAM_KEY:
        abort(500, description="OPENAI_API_KEY is not set")
    return f"Bearer {UPSTREAM_KEY}"


def _build_upstream_headers() -> dict[str, str]:
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() in HOP_HEADERS
    }
    headers["Authorization"] = resolve_upstream_authorization()
    return headers


FORCED_MODEL = "gpt-5.4-nano"


@app.route("/v1/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy_v1(subpath: str):
    url = f"{OPENAI_BASE}/v1/{subpath}"
    headers = _build_upstream_headers()

    # Force model to gpt-4o-mini for all requests that carry a model field
    raw = request.get_data()
    try:
        body = json.loads(raw)
        if "model" in body:
            body["model"] = FORCED_MODEL
        raw = json.dumps(body).encode()
    except Exception:
        pass  # non-JSON body (e.g. multipart) — forward as-is

    try:
        upstream = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            params=request.args,
            data=raw,
            stream=True,
            timeout=(30, 600),
        )
    except requests.RequestException:
        abort(502, description="Upstream request failed")

    def generate() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    out_headers = [
        ("Cache-Control", "no-cache"),
        ("X-Accel-Buffering", "no"),
    ]
    ct = upstream.headers.get("Content-Type")
    if ct:
        out_headers.append(("Content-Type", ct))

    return Response(
        stream_with_context(generate()),
        status=upstream.status_code,
        headers=out_headers,
    )


@app.route("/healthz")
def healthz():
    return {"ok": True}, 200


def main() -> None:
    host = os.environ.get("LISTEN_HOST", "0.0.0.0")
    port = int(os.environ.get("LISTEN_PORT", "1234"))
    if not UPSTREAM_KEY and not _TOKEN_MAP:
        print("error: set OPENAI_API_KEY or PROXY_TOKEN_MAP", file=sys.stderr)
        sys.exit(1)
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()

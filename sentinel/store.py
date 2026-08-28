"""Target list + per-target state, persisted via the Upstash Redis REST API.

Vercel Functions have no persistent local disk, so the web app can't use the
JSON-file state from sentinel/state.py -- it stores everything in Redis
instead. Works with either Vercel KV's env var names or raw Upstash's.
"""
from __future__ import annotations

import json
import os

import requests

TIMEOUT_SECONDS = 10
TARGETS_KEY = "sentinel:targets"


def _rest_url() -> str:
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    if not url:
        raise RuntimeError(
            "No Redis REST URL configured. Set KV_REST_API_URL or "
            "UPSTASH_REDIS_REST_URL (provisioned automatically when you "
            "connect an Upstash Redis store to this Vercel project)."
        )
    return url.rstrip("/")


def _rest_token() -> str:
    token = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not token:
        raise RuntimeError(
            "No Redis REST token configured. Set KV_REST_API_TOKEN or "
            "UPSTASH_REDIS_REST_TOKEN."
        )
    return token


def _command(*args) -> object:
    """Run one Redis command via the Upstash REST API and return its result."""
    resp = requests.post(
        _rest_url(),
        headers={"Authorization": f"Bearer {_rest_token()}"},
        json=list(args),
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"Redis error: {data['error']}")
    return data.get("result")


def _state_key(target_id: str) -> str:
    return f"sentinel:state:{target_id}"


def get_targets() -> list[dict]:
    raw = _command("GET", TARGETS_KEY)
    return json.loads(raw) if raw else []


def save_targets(targets: list[dict]) -> None:
    _command("SET", TARGETS_KEY, json.dumps(targets))


def get_state(target_id: str) -> dict | None:
    raw = _command("GET", _state_key(target_id))
    return json.loads(raw) if raw else None


def save_state(target_id: str, state: dict) -> None:
    _command("SET", _state_key(target_id), json.dumps(state))


def delete_state(target_id: str) -> None:
    _command("DEL", _state_key(target_id))

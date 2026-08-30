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
SETTINGS_KEY = "sentinel:settings"
HISTORY_CAP = 60

DEFAULT_SETTINGS = {
    "webhook_url": None,
    "notify_content_changes": True,
    "notify_fetch_errors": True,
    "notify_every_sweep": False,
}


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


def _history_key(target_id: str) -> str:
    return f"sentinel:history:{target_id}"


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


def append_history(target_id: str, entry: dict) -> None:
    """Record one check outcome. Keeps only the most recent HISTORY_CAP
    entries -- backs the dashboard sparklines, the per-target check-history
    list, and the merged Activity feed, without unbounded growth."""
    key = _history_key(target_id)
    _command("RPUSH", key, json.dumps(entry))
    _command("LTRIM", key, -HISTORY_CAP, -1)


def get_history(target_id: str, limit: int | None = None) -> list[dict]:
    """Most-recent-last list of check entries. `limit` returns only the tail."""
    key = _history_key(target_id)
    start = -limit if limit else 0
    raw = _command("LRANGE", key, start, -1)
    return [json.loads(item) for item in (raw or [])]


def delete_history(target_id: str) -> None:
    _command("DEL", _history_key(target_id))


def get_settings() -> dict:
    raw = _command("GET", SETTINGS_KEY)
    settings = dict(DEFAULT_SETTINGS)
    if raw:
        settings.update(json.loads(raw))
    return settings


def save_settings(settings: dict) -> None:
    merged = get_settings()
    merged.update(settings)
    _command("SET", SETTINGS_KEY, json.dumps(merged))


def count_keys(pattern: str) -> int:
    """Real (not decorative) key count for the Settings storage panel."""
    return len(_command("KEYS", pattern) or [])

"""Sentinel web dashboard -- Flask app deployed as a Vercel Function.

Vercel Functions are stateless and ephemeral (no persistent disk, no
long-running process), so unlike cli.py this reads/writes target config and
last-seen state via sentinel/store.py (Redis) instead of local JSON files.
Automatic polling happens via a Vercel Cron Job hitting /api/cron-check;
the dashboard's "Check now" buttons trigger the same logic on demand.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from flask import Flask, flash, redirect, render_template, request, session, url_for

from sentinel import store
from sentinel.differ import content_hash, diff_lines
from sentinel.fetcher import fetch_content, normalize
from sentinel.notifier import notify_discord, notify_error

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-key")


@app.errorhandler(Exception)
def handle_error(exc: Exception):
    """Show an actionable message instead of a blank Internal Server Error.

    Vercel's default error page gives no detail, and the most common failure
    mode here is a missing setup step (Redis not provisioned, env vars not
    set) -- store.py already raises a RuntimeError with a specific fix for
    those, so surface it directly. Anything else prints to stdout (visible
    in Vercel's Runtime Logs) rather than leaking a traceback to the client.
    """
    if isinstance(exc, RuntimeError):
        message = str(exc)
    else:
        print(f"Unhandled error: {exc!r}")
        message = (
            "Something went wrong processing this request. Check this "
            "project's Runtime Logs in the Vercel dashboard for details."
        )
    return render_template("error.html", message=message), 500


def _password_configured() -> bool:
    return bool(os.environ.get("DASHBOARD_PASSWORD"))


def _is_authed() -> bool:
    return not _password_configured() or session.get("authed") is True


@app.before_request
def _require_login():
    if request.path == "/login" or request.path == "/api/cron-check" or request.path.startswith("/static"):
        return None
    if not _is_authed():
        return redirect(url_for("login"))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _password_configured():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        if request.form.get("password") == os.environ.get("DASHBOARD_PASSWORD"):
            session["authed"] = True
            return redirect(url_for("dashboard"))
        flash("Wrong password.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def dashboard():
    targets = store.get_targets()
    for target in targets:
        target["state"] = store.get_state(target["id"])
    return render_template("dashboard.html", targets=targets, password_set=_password_configured())


@app.route("/targets", methods=["POST"])
def add_target():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    if not name or not url:
        flash("Name and URL are required.")
        return redirect(url_for("dashboard"))

    json_fields_raw = request.form.get("json_fields", "").strip()
    json_fields = [f.strip() for f in json_fields_raw.split(",") if f.strip()] or None

    target = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "url": url,
        "selector": request.form.get("selector", "").strip() or None,
        "json_fields": json_fields,
        "webhook_url": request.form.get("webhook_url", "").strip() or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    targets = store.get_targets()
    targets.append(target)
    store.save_targets(targets)
    flash(f"Added target '{name}'.")
    return redirect(url_for("dashboard"))


@app.route("/targets/<target_id>/delete", methods=["POST"])
def delete_target(target_id: str):
    targets = [t for t in store.get_targets() if t["id"] != target_id]
    store.save_targets(targets)
    store.delete_state(target_id)
    return redirect(url_for("dashboard"))


def _run_check(target: dict) -> None:
    """Fetch, diff against last-seen state, notify on change, save new state."""
    default_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    webhook_url = target.get("webhook_url") or default_webhook
    now = datetime.now(timezone.utc).isoformat()

    try:
        raw_text = fetch_content(target["url"], target.get("selector"), target.get("json_fields"))
    except Exception as exc:
        if webhook_url:
            notify_error(webhook_url, target["name"], target["url"], str(exc))
        store.save_state(
            target["id"],
            {
                "hash": None,
                "content": None,
                "last_checked": now,
                "last_status": "error",
                "last_error": str(exc),
                "last_diff": None,
            },
        )
        return

    new_hash = content_hash(normalize(raw_text))
    previous = store.get_state(target["id"])

    if previous is None or previous.get("hash") is None:
        store.save_state(
            target["id"],
            {
                "hash": new_hash,
                "content": raw_text,
                "last_checked": now,
                "last_status": "baseline",
                "last_error": None,
                "last_diff": None,
            },
        )
        return

    if previous["hash"] == new_hash:
        store.save_state(
            target["id"],
            {
                "hash": new_hash,
                "content": raw_text,
                "last_checked": now,
                "last_status": "unchanged",
                "last_error": None,
                "last_diff": previous.get("last_diff"),
            },
        )
        return

    summary = diff_lines(previous["content"], raw_text)
    description = summary.describe() if not summary.is_empty else "Content changed (formatting-only diff)."
    if webhook_url:
        notify_discord(webhook_url, target["name"], target["url"], description)
    store.save_state(
        target["id"],
        {
            "hash": new_hash,
            "content": raw_text,
            "last_checked": now,
            "last_status": "changed",
            "last_error": None,
            "last_diff": description,
        },
    )


@app.route("/targets/<target_id>/check", methods=["POST"])
def check_one(target_id: str):
    target = next((t for t in store.get_targets() if t["id"] == target_id), None)
    if target:
        _run_check(target)
    return redirect(url_for("dashboard"))


@app.route("/check-all", methods=["POST"])
def check_all():
    for target in store.get_targets():
        _run_check(target)
    return redirect(url_for("dashboard"))


@app.route("/api/cron-check", methods=["GET"])
def cron_check():
    """Endpoint Vercel Cron hits on schedule. Guarded by CRON_SECRET so it
    can't be used by randoms to spam your Discord webhook or re-fetch your
    targets on demand -- Vercel sends this bearer token automatically when
    CRON_SECRET is set as an env var."""
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and request.headers.get("Authorization") != f"Bearer {cron_secret}":
        return {"error": "unauthorized"}, 401

    targets = store.get_targets()
    for target in targets:
        _run_check(target)
    return {"checked": len(targets)}


if __name__ == "__main__":
    app.run(debug=True, port=5000)

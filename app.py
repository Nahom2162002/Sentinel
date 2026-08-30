"""Sentinel web dashboard -- Flask app deployed as a Vercel Function.

Vercel Functions are stateless and ephemeral (no persistent disk, no
long-running process), so unlike cli.py this reads/writes target config and
last-seen state via sentinel/store.py (Redis) instead of local JSON files.
Automatic polling happens externally (cron-job.org primarily, GitHub Actions
and Vercel's own cron as idempotent backups) hitting /api/cron-check; the
dashboard's manual check controls trigger the exact same logic on demand.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from flask import Flask, flash, redirect, render_template, request, session, url_for

from sentinel import store
from sentinel.differ import DiffSummary, content_hash, diff_lines
from sentinel.fetcher import fetch_content, normalize
from sentinel.notifier import notify_discord, notify_error

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-key")

CRON_STALE_AFTER_MINUTES = 15


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


@app.context_processor
def _inject_sidebar_globals():
    """Every authenticated page's sidebar shows the cron health indicator, so
    compute it here once instead of threading it through every render_template
    call. Must never raise: if Redis is unreachable, the error page itself
    goes through render_template too, and a context processor that blows up
    would break the error page's own rendering -- defeating the point of the
    error handler below."""
    password_set = _password_configured()
    if request.path == "/login" or not _is_authed():
        return {"password_set": password_set}
    try:
        targets = store.get_targets()
        for target in targets:
            target["state"] = store.get_state(target["id"])
        health = _cron_health(targets)
    except Exception:
        health = {"active": False, "label": "UNKNOWN"}
    return {"password_set": password_set, "sidebar_health": health}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_webhook(target: dict, settings: dict) -> str | None:
    """Target-level override > Settings-page default > DISCORD_WEBHOOK_URL env."""
    return target.get("webhook_url") or settings.get("webhook_url") or os.environ.get("DISCORD_WEBHOOK_URL")


def _mask_webhook(url: str | None) -> str | None:
    if not url:
        return None
    return "discord.com/api/webhooks/••••" + url[-4:]


def _normalize_diff(last_diff) -> dict:
    """last_diff used to be a pre-rendered truncated string; it's now stored
    as {"added": [...], "removed": [...]} so the detail page can show every
    line. Old Redis entries still have the string shape -- wrap them so
    templates never have to know which shape they got."""
    if last_diff is None:
        return {"added": [], "removed": [], "legacy_text": None}
    if isinstance(last_diff, str):
        return {"added": [], "removed": [], "legacy_text": last_diff}
    return {"added": last_diff.get("added", []), "removed": last_diff.get("removed", []), "legacy_text": None}


def _diff_preview_text(diff: dict, max_lines: int = 5) -> str:
    if diff.get("legacy_text"):
        return diff["legacy_text"]
    summary = DiffSummary(added=diff.get("added", []), removed=diff.get("removed", []))
    return summary.describe(max_lines=max_lines) if not summary.is_empty else ""


def _format_ago(iso_str: str | None) -> str:
    if not iso_str:
        return "never"
    dt = datetime.fromisoformat(iso_str)
    minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _cron_health(targets: list[dict]) -> dict:
    """Derived from real check timestamps, not a decorative always-on dot --
    this project has already hit silent cron failures once; a health
    indicator that can't actually go stale isn't worth having."""
    checked_times = [t["state"]["last_checked"] for t in targets if t.get("state") and t["state"].get("last_checked")]
    if not checked_times:
        return {"active": False, "label": "NO CHECKS YET"}
    latest = max(checked_times)
    age_minutes = (datetime.now(timezone.utc) - datetime.fromisoformat(latest)).total_seconds() / 60
    active = age_minutes <= CRON_STALE_AFTER_MINUTES
    return {"active": active, "label": "CRON ACTIVE" if active else "CRON STALE", "last_ago": _format_ago(latest)}


def _pad_sparkline(history: list[dict], width: int = 12) -> list[dict]:
    pad = max(0, width - len(history))
    return [{"status": "never"} for _ in range(pad)] + history[-width:]


app.jinja_env.filters["ago"] = _format_ago


# ---------------------------------------------------------------------------
# Targets list
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    filter_ = request.args.get("filter", "ALL").upper()
    targets = store.get_targets()

    for target in targets:
        state = store.get_state(target["id"])
        target["state"] = state
        if state:
            state["diff"] = _normalize_diff(state.get("last_diff"))
            state["diff_preview"] = _diff_preview_text(state["diff"])
        target["sparkline"] = _pad_sparkline(store.get_history(target["id"], limit=12))

    if filter_ != "ALL":
        visible = [t for t in targets if t.get("state") and t["state"]["last_status"] == filter_.lower()]
    else:
        visible = targets

    changed_count = sum(1 for t in targets if t.get("state") and t["state"]["last_status"] == "changed")
    error_count = sum(1 for t in targets if t.get("state") and t["state"]["last_status"] == "error")
    last_checked = [t["state"]["last_checked"] for t in targets if t.get("state") and t["state"].get("last_checked")]

    return render_template(
        "dashboard.html",
        active_nav="targets",
        targets=visible,
        all_count=len(targets),
        changed_count=changed_count,
        error_count=error_count,
        last_sweep=_format_ago(max(last_checked)) if last_checked else "never",
        current_filter=filter_,
    )


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
    store.delete_history(target_id)
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Checking targets (manual buttons and the automated /api/cron-check share
# this exact logic -- automation is just this function invoked on schedule)
# ---------------------------------------------------------------------------

def _run_check(target: dict, settings: dict) -> None:
    webhook_url = _resolve_webhook(target, settings)
    now = datetime.now(timezone.utc).isoformat()

    try:
        raw_text = fetch_content(target["url"], target.get("selector"), target.get("json_fields"))
    except Exception as exc:
        error_text = str(exc)
        notified = bool(webhook_url and settings.get("notify_fetch_errors", True))
        if notified:
            notify_error(webhook_url, target["name"], target["url"], error_text)
        store.save_state(target["id"], {
            "hash": None, "content": None, "last_checked": now,
            "last_status": "error", "last_error": error_text, "last_diff": None,
        })
        store.append_history(target["id"], {
            "time": now, "status": "error",
            "note": error_text + (" → Discord" if notified else ""),
        })
        return

    new_hash = content_hash(normalize(raw_text))
    previous = store.get_state(target["id"])

    if previous is None or previous.get("hash") is None:
        store.save_state(target["id"], {
            "hash": new_hash, "content": raw_text, "last_checked": now,
            "last_status": "baseline", "last_error": None, "last_diff": None,
        })
        store.append_history(target["id"], {"time": now, "status": "baseline", "note": "Baseline recorded"})
        return

    if previous["hash"] == new_hash:
        notified = bool(webhook_url and settings.get("notify_every_sweep", False))
        if notified:
            notify_discord(webhook_url, target["name"], target["url"], "No changes detected on this sweep.")
        store.save_state(target["id"], {
            "hash": new_hash, "content": raw_text, "last_checked": now,
            "last_status": "unchanged", "last_error": None, "last_diff": previous.get("last_diff"),
        })
        store.append_history(target["id"], {
            "time": now, "status": "unchanged",
            "note": "Hash matched" + (" → Discord" if notified else ""),
        })
        return

    summary = diff_lines(previous["content"], raw_text)
    description = summary.describe() if not summary.is_empty else "Content changed (formatting-only diff)."
    notified = bool(webhook_url and settings.get("notify_content_changes", True))
    if notified:
        notify_discord(webhook_url, target["name"], target["url"], description)
    store.save_state(target["id"], {
        "hash": new_hash, "content": raw_text, "last_checked": now,
        "last_status": "changed", "last_error": None,
        "last_diff": {"added": summary.added, "removed": summary.removed},
    })
    note = f"+{len(summary.added)} added, -{len(summary.removed)} removed"
    store.append_history(target["id"], {
        "time": now, "status": "changed",
        "note": note + (" → Discord" if notified else " (notification skipped)"),
    })


def _redirect_next(default_endpoint: str = "dashboard"):
    return redirect(request.form.get("next") or url_for(default_endpoint))


@app.route("/targets/<target_id>/check", methods=["POST"])
def check_one(target_id: str):
    target = next((t for t in store.get_targets() if t["id"] == target_id), None)
    if target:
        _run_check(target, store.get_settings())
    return _redirect_next()


@app.route("/check-all", methods=["POST"])
def check_all():
    settings = store.get_settings()
    for target in store.get_targets():
        _run_check(target, settings)
    return _redirect_next()


@app.route("/api/cron-check", methods=["GET"])
def cron_check():
    """Endpoint the external schedulers hit. Guarded by CRON_SECRET so it
    can't be used by randoms to spam your Discord webhook or re-fetch your
    targets on demand -- each scheduler sends this bearer token because you
    configured it to, not because Vercel/GitHub/cron-job.org know it."""
    cron_secret = os.environ.get("CRON_SECRET")
    if cron_secret and request.headers.get("Authorization") != f"Bearer {cron_secret}":
        return {"error": "unauthorized"}, 401

    settings = store.get_settings()
    targets = store.get_targets()
    for target in targets:
        _run_check(target, settings)
    return {"checked": len(targets)}


# ---------------------------------------------------------------------------
# Target detail
# ---------------------------------------------------------------------------

@app.route("/targets/<target_id>")
def target_detail(target_id: str):
    target = next((t for t in store.get_targets() if t["id"] == target_id), None)
    if not target:
        flash("That target no longer exists.")
        return redirect(url_for("dashboard"))

    state = store.get_state(target_id)
    diff = _normalize_diff(state.get("last_diff")) if state else {"added": [], "removed": [], "legacy_text": None}
    history = list(reversed(store.get_history(target_id, limit=6)))
    settings = store.get_settings()

    return render_template(
        "target_detail.html",
        active_nav="targets",
        target=target,
        state=state,
        diff=diff,
        history=history,
        mode="JSON feed" if target.get("json_fields") else "HTML page",
        webhook_masked=_mask_webhook(_resolve_webhook(target, settings)),
    )


# ---------------------------------------------------------------------------
# Activity -- merged check history across every target
# ---------------------------------------------------------------------------

@app.route("/activity")
def activity():
    targets = store.get_targets()
    entries = []
    for target in targets:
        for entry in store.get_history(target["id"], limit=50):
            entries.append({**entry, "target_name": target["name"]})
    entries.sort(key=lambda e: e["time"], reverse=True)

    return render_template(
        "activity.html",
        active_nav="activity",
        entries=entries[:50],
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        if request.form.get("action") == "reset_baselines":
            for target in store.get_targets():
                store.delete_state(target["id"])
                store.delete_history(target["id"])
            flash("All baselines reset -- the next sweep will re-baseline every target.")
            return redirect(url_for("settings_page"))

        new_settings = {
            "notify_content_changes": "notify_content_changes" in request.form,
            "notify_fetch_errors": "notify_fetch_errors" in request.form,
            "notify_every_sweep": "notify_every_sweep" in request.form,
        }
        if request.form.get("clear_webhook"):
            new_settings["webhook_url"] = None
        else:
            webhook_input = request.form.get("webhook_url", "").strip()
            if webhook_input:
                new_settings["webhook_url"] = webhook_input
        store.save_settings(new_settings)
        flash("Settings saved.")
        return redirect(url_for("settings_page"))

    settings = store.get_settings()
    return render_template(
        "settings.html",
        active_nav="settings",
        settings=settings,
        webhook_masked=_mask_webhook(settings.get("webhook_url") or os.environ.get("DISCORD_WEBHOOK_URL")),
        target_count=len(store.get_targets()),
        state_key_count=store.count_keys("sentinel:state:*"),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)

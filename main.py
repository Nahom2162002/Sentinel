#!/usr/bin/env python3
"""
Sentinel: watch a page (or a few), notify Discord when it changes.

Usage:
    python main.py --once                 # single check pass, good for cron
    python main.py --loop --interval 300   # poll forever, every 300s
    python main.py --once --target "Example Job Board Search"
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
import os

from sentinel.config import load_config, Target
from sentinel.fetcher import fetch_content, normalize
from sentinel.differ import content_hash, diff_lines
from sentinel.state import load_state, save_state, TargetState
from sentinel.notifier import notify_discord, notify_error


def check_target(target: Target, state_dir: str, verbose: bool) -> None:
    if verbose:
        print(f"[{target.name}] checking {target.url}")

    try:
        raw_text = fetch_content(target.url, target.selector)
    except Exception as exc:  # network error, bad selector, non-200, etc.
        print(f"[{target.name}] ERROR: {exc}", file=sys.stderr)
        notify_error(target.webhook_url, target.name, target.url, str(exc))
        return

    new_hash = content_hash(normalize(raw_text))
    now = datetime.now(timezone.utc).isoformat()
    previous = load_state(state_dir, target.name)

    if previous is None:
        # First run for this target: establish a baseline, no notification.
        save_state(state_dir, target.name, TargetState(hash=new_hash, content=raw_text, last_checked=now))
        print(f"[{target.name}] baseline established, {len(raw_text)} chars")
        return

    if previous.hash == new_hash:
        save_state(state_dir, target.name, TargetState(hash=new_hash, content=raw_text, last_checked=now))
        if verbose:
            print(f"[{target.name}] no change")
        return

    summary = diff_lines(previous.content, raw_text)
    description = summary.describe() if not summary.is_empty else "Content hash changed (formatting-only diff)."
    notify_discord(target.webhook_url, target.name, target.url, description)
    save_state(state_dir, target.name, TargetState(hash=new_hash, content=raw_text, last_checked=now))
    print(f"[{target.name}] CHANGE DETECTED -> notified Discord")


def run_pass(targets: list[Target], state_dir: str, verbose: bool) -> None:
    for target in targets:
        check_target(target, state_dir, verbose)


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Watch pages and notify Discord on change.")
    parser.add_argument("--config", default="config.json", help="Path to config file (default: config.json)")
    parser.add_argument("--state-dir", default="state", help="Directory for last-seen state (default: state/)")
    parser.add_argument("--target", help="Only check the target with this exact name")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between polls in --loop mode (default: 300)")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-check logging")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run a single check pass and exit (use with cron)")
    mode.add_argument("--loop", action="store_true", help="Poll forever, sleeping --interval seconds between passes")

    args = parser.parse_args()

    default_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    targets = load_config(args.config, default_webhook)

    if args.target:
        targets = [t for t in targets if t.name == args.target]
        if not targets:
            print(f"No target named '{args.target}' in {args.config}", file=sys.stderr)
            sys.exit(1)

    verbose = not args.quiet

    if args.once:
        run_pass(targets, args.state_dir, verbose)
        return

    print(f"Polling {len(targets)} target(s) every {args.interval}s. Ctrl+C to stop.")
    try:
        while True:
            run_pass(targets, args.state_dir, verbose)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

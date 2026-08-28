"""Loads and validates the watch-target config file."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass


@dataclass
class Target:
    name: str
    url: str
    selector: str | None
    webhook_url: str | None


def load_config(path: str, default_webhook_url: str | None) -> list[Target]:
    if not os.path.exists(path):
        print(
            f"Config file not found: {path}\n"
            f"Copy config.example.json to {path} and fill in your target(s).",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    targets = []
    for i, entry in enumerate(raw.get("targets", [])):
        if "name" not in entry or "url" not in entry:
            print(f"Target #{i} is missing required field 'name' or 'url'", file=sys.stderr)
            sys.exit(1)

        webhook_url = entry.get("webhook_url") or default_webhook_url
        if not webhook_url:
            print(
                f"Target '{entry['name']}' has no webhook_url and no "
                f"DISCORD_WEBHOOK_URL default is set in .env",
                file=sys.stderr,
            )
            sys.exit(1)

        targets.append(
            Target(
                name=entry["name"],
                url=entry["url"],
                selector=entry.get("selector"),
                webhook_url=webhook_url,
            )
        )

    if not targets:
        print("Config has no targets defined.", file=sys.stderr)
        sys.exit(1)

    return targets

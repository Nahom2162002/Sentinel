"""Posts change notifications to a Discord webhook."""
from __future__ import annotations

import requests

DESCRIPTION_LIMIT = 4000  # Discord embed description max is 4096; leave headroom


def notify_discord(webhook_url: str, target_name: str, target_url: str, description: str) -> None:
    if len(description) > DESCRIPTION_LIMIT:
        description = description[: DESCRIPTION_LIMIT - 20] + "\n... (truncated)"

    payload = {
        "embeds": [
            {
                "title": f"Change detected: {target_name}",
                "url": target_url,
                "description": description,
                "color": 0x00B894,
            }
        ]
    }
    resp = requests.post(webhook_url, json=payload, timeout=10)
    resp.raise_for_status()


def notify_error(webhook_url: str, target_name: str, target_url: str, error: str) -> None:
    payload = {
        "embeds": [
            {
                "title": f"Watcher error: {target_name}",
                "url": target_url,
                "description": f"```\n{error[:1900]}\n```",
                "color": 0xD63031,
            }
        ]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException:
        pass  # don't let a failed error-notification crash the watcher

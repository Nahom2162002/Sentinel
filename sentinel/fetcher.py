"""Fetches a page and extracts the text of the block we care about."""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Sentinel-Watcher/1.0 (+https://github.com/) personal change-watcher"
TIMEOUT_SECONDS = 15


def fetch_content(url: str, selector: str | None) -> str:
    """Fetch `url` and return normalized text for `selector` (or the whole body)."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    if selector:
        node = soup.select_one(selector)
        if node is None:
            raise ValueError(f"Selector '{selector}' matched nothing on {url}")
    else:
        node = soup.body or soup

    for tag in node.select("script, style"):
        tag.decompose()

    text = node.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def normalize(text: str) -> str:
    """Collapse whitespace so trivial formatting churn doesn't count as a change."""
    return re.sub(r"\s+", " ", text).strip()

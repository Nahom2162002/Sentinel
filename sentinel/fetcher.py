"""Fetches a page and extracts the text of the block we care about."""
from __future__ import annotations

import json
import re

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Sentinel-Watcher/1.0 (+https://github.com/) personal change-watcher"
TIMEOUT_SECONDS = 15


def fetch_content(url: str, selector: str | None, json_fields: list[str] | None = None) -> str:
    """Fetch `url` and return normalized text for `selector` (or the whole body).

    If `url` points at a JSON feed (path ends in `.json`), extract one line per
    list item instead of parsing HTML -- see `_extract_json_lines`.
    """
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()

    if url.split("?", 1)[0].endswith(".json"):
        return _extract_json_lines(resp.json(), json_fields)

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


def _extract_json_lines(data, json_fields: list[str] | None) -> str:
    """Turn a JSON feed (a list of objects, e.g. a job postings API) into one
    line per item, so `differ.diff_lines` can report added/removed entries.

    Items missing every field in `json_fields` are skipped -- this drops feed
    metadata objects (e.g. RemoteOK's leading legal-notice entry) that aren't
    real listings.
    """
    if not isinstance(data, list):
        return json.dumps(data, sort_keys=True)

    lines = []
    for item in data:
        if not isinstance(item, dict):
            lines.append(json.dumps(item, sort_keys=True))
            continue
        if json_fields:
            if not any(f in item for f in json_fields):
                continue
            values = [str(item[f]) for f in json_fields if f in item]
            lines.append(" | ".join(values))
        else:
            lines.append(json.dumps(item, sort_keys=True))
    return "\n".join(lines)

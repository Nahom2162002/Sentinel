"""Hashing + line-level diff summary between old and new content."""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class DiffSummary:
    added: list[str]
    removed: list[str]

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed

    def describe(self, max_lines: int = 5) -> str:
        parts = []
        if self.added:
            parts.append(f"**+{len(self.added)} added**")
        if self.removed:
            parts.append(f"**-{len(self.removed)} removed**")
        header = ", ".join(parts) if parts else "content changed"

        sample_lines = []
        for line in self.added[:max_lines]:
            sample_lines.append(f"+ {line[:200]}")
        for line in self.removed[:max_lines]:
            sample_lines.append(f"- {line[:200]}")

        body = "\n".join(sample_lines)
        return f"{header}\n{body}" if body else header


def diff_lines(old_text: str, new_text: str) -> DiffSummary:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added, removed = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed.extend(old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])

    return DiffSummary(added=added, removed=removed)

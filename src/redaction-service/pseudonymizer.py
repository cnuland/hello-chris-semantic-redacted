"""Deterministic pseudonymization: replace PII spans with type-indexed placeholders."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DetectedEntity:
    """A single entity detected in the source text."""

    entity_type: str
    start: int
    end: int
    score: float
    original: str = ""


@dataclass
class MappingEntry:
    """A stored mapping between placeholders and original values."""

    forward: dict[str, str] = field(default_factory=dict)   # original -> placeholder
    reverse: dict[str, str] = field(default_factory=dict)   # placeholder -> original
    created_at: float = field(default_factory=time.time)


class PseudonymMapper:
    """Thread-safe, in-memory pseudonymization mapper.

    Each call to ``create_mapping`` produces a unique ``mapping_id`` that
    can later be used by ``restore_text`` to reverse the redaction.
    Mappings are never persisted and are automatically evicted after
    ``ttl_seconds``.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._store: dict[str, MappingEntry] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_mapping(
        self, entities: list[DetectedEntity]
    ) -> tuple[str, MappingEntry]:
        """Build a deterministic forward/reverse map for *entities*.

        Returns ``(mapping_id, entry)`` where ``mapping_id`` is a short
        UUID-based key prefixed with ``m-``.
        """
        self._evict_expired()

        type_counters: dict[str, int] = {}
        entry = MappingEntry()

        # Sort entities by start offset so numbering is stable.
        for ent in sorted(entities, key=lambda e: e.start):
            original = ent.original
            if original in entry.forward:
                # Same literal already mapped -- reuse placeholder.
                continue
            count = type_counters.get(ent.entity_type, 0) + 1
            type_counters[ent.entity_type] = count
            placeholder = f"<{ent.entity_type}_{count}>"
            entry.forward[original] = placeholder
            entry.reverse[placeholder] = original

        mapping_id = f"m-{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._store[mapping_id] = entry
        return mapping_id, entry

    def apply_redaction(self, text: str, entry: MappingEntry) -> str:
        """Replace all originals in *text* with their placeholders.

        Replacements are applied longest-first to avoid partial matches.
        """
        result = text
        # Replace longest originals first to prevent partial overlap.
        for original in sorted(entry.forward, key=len, reverse=True):
            result = result.replace(original, entry.forward[original])
        return result

    def restore_text(
        self, text: str, mapping_id: str
    ) -> tuple[str, int, bool]:
        """Restore placeholders in *text* using the stored mapping.

        Returns ``(restored_text, placeholders_restored, mapping_deleted)``.
        Raises ``KeyError`` if *mapping_id* is unknown or expired.
        """
        self._evict_expired()

        with self._lock:
            entry = self._store.pop(mapping_id, None)
        if entry is None:
            raise KeyError(f"Unknown or expired mapping_id: {mapping_id}")

        restored = text
        count = 0
        # Replace longest placeholders first.
        for placeholder in sorted(entry.reverse, key=len, reverse=True):
            if placeholder in restored:
                restored = restored.replace(placeholder, entry.reverse[placeholder])
                count += 1
        return restored, count, True

    def get_mapping(self, mapping_id: str) -> MappingEntry | None:
        """Peek at a mapping without consuming it (used by /scan)."""
        self._evict_expired()
        with self._lock:
            return self._store.get(mapping_id)

    @property
    def active_mappings(self) -> int:
        """Return the number of mappings currently held."""
        with self._lock:
            return len(self._store)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict_expired(self) -> None:
        """Remove mappings older than TTL."""
        now = time.time()
        with self._lock:
            expired = [
                mid
                for mid, entry in self._store.items()
                if now - entry.created_at > self._ttl
            ]
            for mid in expired:
                del self._store[mid]

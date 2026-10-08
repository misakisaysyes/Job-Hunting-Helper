"""Callbacks used by the BOSS collector."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Callable

from collector.models import JobCandidate


@dataclass
class CollectorHooks:
    """Callbacks supplied by the collection runner."""

    stop_event: Event | None
    on_list_candidate: Callable[[JobCandidate], bool]
    on_candidate: Callable[[JobCandidate], bool]
    on_parse_failed: Callable[[str], None]
    on_event: Callable[..., None]
    # Called after a complete job passes the detail prefilter and is accepted.
    on_prefilter_pass: Callable[[JobCandidate], None] = lambda job: None
    target_reached: Callable[[], bool] = lambda: False
    on_cycle_complete: Callable[[], None] = lambda: None
    target_count: int = 0
    # Continuing a scan does not imply every candidate was saved reliably.
    can_checkpoint: Callable[[], bool] = lambda: True
    # Checkpoints belong to one explicitly selected run, never to global searches.
    completed_page: Callable[[str, str], int] = lambda city, keyword: 0
    on_page_complete: Callable[[str, str, int], None] = lambda city, keyword, page: None

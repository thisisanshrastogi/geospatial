"""File processing lifecycle as an explicit state machine.

Only the transitions below are legal:
    PENDING -> {PROCESSING, FAILED}
    PROCESSING -> {COMPLETED, FAILED}

Anything else raises `InvalidStatusTransition` (defined in errors.py).
Making this explicit prevents bugs like marking a COMPLETED file PENDING.
"""

from __future__ import annotations

from enum import StrEnum


class FileStatus(StrEnum):
    """Possible states of an uploaded file."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Allowed next-states per current state. Terminal states map to empty sets.
_ALLOWED_TRANSITIONS: dict[FileStatus, frozenset[FileStatus]] = {
    FileStatus.PENDING: frozenset({FileStatus.PROCESSING, FileStatus.FAILED}),
    FileStatus.PROCESSING: frozenset({FileStatus.COMPLETED, FileStatus.FAILED}),
    FileStatus.COMPLETED: frozenset(),
    FileStatus.FAILED: frozenset(),
}


def can_transition(current: FileStatus, target: FileStatus) -> bool:
    """Return True if `current -> target` is a legal transition."""
    return target in _ALLOWED_TRANSITIONS[current]


def allowed_transitions(current: FileStatus) -> frozenset[FileStatus]:
    """Return the legal next-states for `current` (empty if terminal)."""
    return _ALLOWED_TRANSITIONS[current]

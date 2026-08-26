from __future__ import annotations

from enum import StrEnum


class WorkflowKind(StrEnum):
    apply = "apply"
    follow_up = "follow_up"


class RunState(StrEnum):
    running = "running"
    waiting_approval = "waiting_approval"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    skipped = "skipped"
    waiting = "waiting"
    rejected = "rejected"
    failed = "failed"


TERMINAL_RUN_STATES = {RunState.completed, RunState.failed, RunState.cancelled}

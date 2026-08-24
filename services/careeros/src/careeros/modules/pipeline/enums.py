from __future__ import annotations

from enum import StrEnum


class PipelineKind(StrEnum):
    employment = "employment"
    freelance = "freelance"


class Stage(StrEnum):
    # employment
    inbox = "inbox"
    interested = "interested"
    preparing = "preparing"
    applied = "applied"
    recruiter_screen = "recruiter_screen"
    technical = "technical"
    final = "final"
    offer = "offer"
    rejected = "rejected"
    archived = "archived"
    # freelance
    lead = "lead"
    discovery = "discovery"
    proposal = "proposal"
    negotiation = "negotiation"
    active = "active"
    won = "won"
    lost = "lost"


STAGES: dict[PipelineKind, list[Stage]] = {
    PipelineKind.employment: [
        Stage.inbox,
        Stage.interested,
        Stage.preparing,
        Stage.applied,
        Stage.recruiter_screen,
        Stage.technical,
        Stage.final,
        Stage.offer,
        Stage.rejected,
        Stage.archived,
    ],
    PipelineKind.freelance: [
        Stage.lead,
        Stage.discovery,
        Stage.proposal,
        Stage.negotiation,
        Stage.active,
        Stage.won,
        Stage.lost,
        Stage.archived,
    ],
}

TERMINAL_STAGES = {Stage.rejected, Stage.archived, Stage.won, Stage.lost}
APPLIED_STAGES = {Stage.applied, Stage.proposal}


class EventKind(StrEnum):
    discovered = "discovered"
    stage_change = "stage_change"
    applied = "applied"
    message_sent = "message_sent"
    message_received = "message_received"
    follow_up = "follow_up"
    interview_scheduled = "interview_scheduled"
    interview_done = "interview_done"
    feedback = "feedback"
    offer = "offer"
    note = "note"


class InterviewKind(StrEnum):
    recruiter_screen = "recruiter_screen"
    technical = "technical"
    system_design = "system_design"
    take_home = "take_home"
    final = "final"
    client_call = "client_call"
    other = "other"


class InterviewOutcome(StrEnum):
    pending = "pending"
    passed = "passed"
    failed = "failed"
    canceled = "canceled"

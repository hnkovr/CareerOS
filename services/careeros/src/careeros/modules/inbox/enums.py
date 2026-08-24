from __future__ import annotations

from enum import StrEnum


class MessageClass(StrEnum):
    new_opportunity = "new_opportunity"
    recruiter_outreach = "recruiter_outreach"
    client_lead = "client_lead"
    interview = "interview"
    application_update = "application_update"
    rejection = "rejection"
    offer = "offer"
    platform_notification = "platform_notification"
    follow_up_required = "follow_up_required"
    spam_noise = "spam_noise"
    other = "other"


class Urgency(StrEnum):
    high = "high"
    normal = "normal"
    low = "low"


class Direction(StrEnum):
    inbound = "inbound"
    outbound = "outbound"


class MailboxProvider(StrEnum):
    manual = "manual"
    gmail = "gmail"


ATTENTION_CLASSES = {
    MessageClass.new_opportunity,
    MessageClass.recruiter_outreach,
    MessageClass.client_lead,
    MessageClass.interview,
    MessageClass.offer,
    MessageClass.follow_up_required,
}

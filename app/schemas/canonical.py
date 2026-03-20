from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field


class Organization(BaseModel):
    source_id: str
    name: str | None = None


class Person(BaseModel):
    source_id: str
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class Team(BaseModel):
    source_id: str
    name: str | None = None


class Queue(BaseModel):
    source_id: str
    name: str | None = None


class Contract(BaseModel):
    source_id: str
    organization_id: str | None = None
    name: str | None = None


class Address(BaseModel):
    source_id: str
    organization_id: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class SlaInfo(BaseModel):
    attend_expiration: str | None = None
    solve_expiration: str | None = None
    attend_sla: bool | None = None
    solved_in_time: bool | None = None


class Ticket(BaseModel):
    source_id: str
    organization_id: str | None = None
    requester_id: str | None = None
    owner_id: str | None = None
    team_id: str | None = None
    queue_id: str | None = None
    contract_id: str | None = None
    address_id: str | None = None
    status: str | None = None
    stage: str | None = None
    priority: str | None = None
    subject: str | None = None
    created_at: str | None = None
    sla_info: SlaInfo | None = None
    followers: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class TicketEvent(BaseModel):
    source_id: str
    ticket_id: str
    author_id: str | None = None
    occurred_at: dt.datetime | None = None
    event_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class TicketAttachment(BaseModel):
    source_id: str
    ticket_id: str | None = None
    file_name: str | None = None
    raw_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatThread(BaseModel):
    source_id: str
    ticket_id: str | None = None
    participants: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


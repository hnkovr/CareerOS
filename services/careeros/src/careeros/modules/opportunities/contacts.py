"""Contacts & companies (owned by the opportunities context in P0/P1; brief §34 — minimal CRM)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.opportunities.models import Company, Contact

router = APIRouter(tags=["contacts"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

RELATIONSHIPS = ("recruiter", "hiring_manager", "client", "peer", "other")


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company_id: uuid.UUID | None = None
    company_name: str | None = Field(default=None, description="creates/links the company by name")
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    relationship: str = Field(default="other", pattern="|".join(RELATIONSHIPS))
    next_action: str | None = None
    notes: str | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    relationship: str | None = Field(default=None, pattern="|".join(RELATIONSHIPS))
    last_contact_at: datetime | None = None
    next_action: str | None = None
    notes: str | None = None


class ContactOut(BaseModel):
    id: uuid.UUID
    name: str
    company_id: uuid.UUID | None
    company_name: str | None
    role: str | None
    email: str | None
    linkedin_url: str | None
    relationship: str
    last_contact_at: datetime | None
    next_action: str | None
    notes: str | None
    created_at: datetime


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    domain: str | None
    industry: str | None
    notes: str | None


async def get_or_create_company(session: AsyncSession, user_id: uuid.UUID, name: str) -> Company:
    row = await session.scalar(select(Company).where(Company.name.ilike(name)))
    if row is None:
        row = Company(user_id=user_id, name=name)
        session.add(row)
        await session.flush()
    return row


def _out(c: Contact, company_name: str | None) -> ContactOut:
    return ContactOut(
        id=c.id,
        name=c.name,
        company_id=c.company_id,
        company_name=company_name,
        role=c.role,
        email=c.email,
        linkedin_url=c.linkedin_url,
        relationship=c.relationship_kind,
        last_contact_at=c.last_contact_at,
        next_action=c.next_action,
        notes=c.notes,
        created_at=c.created_at,
    )


async def _company_name(session: AsyncSession, company_id: uuid.UUID | None) -> str | None:
    if company_id is None:
        return None
    company = await session.get(Company, company_id)
    return company.name if company else None


@router.get("/contacts", response_model=list[ContactOut])
async def list_contacts(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    q: str | None = None,
    limit: int = 100,
) -> list[ContactOut]:
    _ = request
    stmt = select(Contact).order_by(Contact.created_at.desc()).limit(limit)
    if q:
        stmt = stmt.where(Contact.name.ilike(f"%{q}%") | Contact.email.ilike(f"%{q}%"))
    rows = (await session.scalars(stmt)).all()
    return [_out(c, await _company_name(session, c.company_id)) for c in rows]


@router.post("/contacts", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
async def create_contact(
    req: ContactIn, request: Request, user: CurrentUserDep, session: SessionDep
) -> ContactOut:
    _ = request
    company_id = req.company_id
    company_name = None
    if company_id is None and req.company_name:
        company = await get_or_create_company(session, user.id, req.company_name)
        company_id, company_name = company.id, company.name
    contact = Contact(
        user_id=user.id,
        name=req.name,
        company_id=company_id,
        role=req.role,
        email=req.email,
        linkedin_url=req.linkedin_url,
        relationship_kind=req.relationship,
        next_action=req.next_action,
        notes=req.notes,
    )
    session.add(contact)
    await session.commit()
    return _out(contact, company_name or await _company_name(session, company_id))


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    req: ContactUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ContactOut:
    _ = request, user
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    data = req.model_dump(exclude_unset=True)
    if "relationship" in data:
        contact.relationship_kind = data.pop("relationship")
    for key, value in data.items():
        setattr(contact, key, value)
    await session.commit()
    return _out(contact, await _company_name(session, contact.company_id))


@router.get("/companies", response_model=list[CompanyOut])
async def list_companies(
    request: Request, user: CurrentUserDep, session: SessionDep, limit: int = 100
) -> list[CompanyOut]:
    _ = request, user
    rows = (await session.scalars(select(Company).order_by(Company.name).limit(limit))).all()
    return [
        CompanyOut(id=c.id, name=c.name, domain=c.domain, industry=c.industry, notes=c.notes)
        for c in rows
    ]

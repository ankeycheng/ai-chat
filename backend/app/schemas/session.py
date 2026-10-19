from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import StrictModel


class SessionCreate(StrictModel):
    title: str | None = Field(None, max_length=200)


class SessionUpdate(StrictModel):
    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("title must not be blank")
        return v


class SearchMatch(BaseModel):
    field: Literal["title", "message"]
    snippet: str
    message_id: str | None = None
    role: str | None = None


class SessionOut(BaseModel):
    id: str
    title: str
    title_source: str
    created_at: datetime
    updated_at: datetime
    last_event_id: str
    status: str
    active_request_id: str | None
    version: int
    match: SearchMatch | None = None


class SessionListOut(BaseModel):
    items: list[SessionOut]
    next_cursor: str | None = None

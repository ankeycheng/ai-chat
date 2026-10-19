import uuid
from datetime import datetime

from pydantic import BaseModel, Field, StrictBool, field_validator

from app.schemas.common import StrictModel


class MessageCreate(StrictModel):
    content: str = Field(..., max_length=32000)
    thinking: StrictBool = False
    web_search: StrictBool = False
    request_id: str

    @field_validator("content")
    @classmethod
    def _content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content must not be blank")
        return v

    @field_validator("request_id")
    @classmethod
    def _rid(cls, v: str) -> str:
        try:
            return str(uuid.UUID(v))
        except (ValueError, AttributeError, TypeError) as e:
            raise ValueError("request_id must be a UUID") from e


class Source(BaseModel):
    id: str
    title: str
    url: str
    snippet: str = ""
    site_name: str | None = None
    site_icon: str | None = None
    date: str | None = None


class MessageOut(BaseModel):
    id: str
    session_id: str
    sequence_no: int
    role: str
    content: str
    reasoning_content: str
    sources: list[Source] = []
    finish_reason: str | None = None
    request_id: str
    created_at: datetime


class AcceptOut(BaseModel):
    accepted: bool = True
    session_id: str
    request_id: str
    status: str
    user_message_id: str | None = None


class MessageListOut(BaseModel):
    """Paginated / filtered history. Always wrapped (not a bare list).

    Query params on GET /messages:
      - after_sequence_no: only sequence_no > N (incremental sync)
      - before_sequence_no: only sequence_no < N (older page; with limit, take nearest below)
      - limit: max items (1..200); when alone, returns the latest N messages
    """

    items: list[MessageOut]
    oldest_sequence_no: int | None = None
    newest_sequence_no: int | None = None
    has_more_before: bool = False

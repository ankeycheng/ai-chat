from datetime import datetime

from pydantic import BaseModel


class RunOut(BaseModel):
    session_id: str
    request_id: str
    status: str
    thinking: bool
    web_search: bool
    user_message_id: str | None
    assistant_message_id: str | None
    start_event_id: str | None
    terminal_event_id: str | None
    terminal_event_published: bool
    finish_reason: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    heartbeat_at: datetime | None
    finished_at: datetime | None


class ActiveRunOut(BaseModel):
    active: bool
    request_id: str | None = None
    status: str | None = None
    start_event_id: str | None = None
    thinking: bool | None = None
    web_search: bool | None = None
    user_message_id: str | None = None
    last_event_id: str | None = None

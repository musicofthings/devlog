from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionEvent:
    timestamp: datetime
    user_message: str | None = None
    tool_name: str | None = None
    file_path: str | None = None
    bash_command: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0


@dataclass
class RawSession:
    session_id: str
    project_path: str
    source: str
    start_time: datetime
    end_time: datetime
    events: list[SessionEvent] = field(default_factory=list)


@dataclass
class SessionDigest:
    session_id: str
    project_path: str
    source: str
    start_time: datetime
    end_time: datetime
    user_messages: list[str] = field(default_factory=list)
    tool_calls: dict[str, int] = field(default_factory=dict)
    files_touched: set[str] = field(default_factory=set)
    bash_commands: list[str] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cache_read: int = 0
    active_minutes: float | None = None
    active_intervals: list[tuple[datetime, datetime]] = field(default_factory=list, repr=False)

    @property
    def duration_minutes(self) -> float:
        if self.active_minutes is not None:
            return max(0.0, self.active_minutes)
        return max(0.0, (self.end_time - self.start_time).total_seconds() / 60.0)

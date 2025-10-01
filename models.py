"""Domain models used by the raid bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


def _parse_offsets(raw: str) -> tuple[int, ...]:
    if not raw:
        return ()
    return tuple(int(part) for part in raw.split(",") if part)


@dataclass(slots=True)
class WaitlistEntry:
    raid_id: int
    user_id: int
    role_name: str
    created_at: int


@dataclass(slots=True)
class Raid:
    id: int
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    name: str
    starts_at: int
    comment: str
    max_participants: int
    created_by: int
    created_at: int
    reminder_offsets: str

    @property
    def starts_dt(self) -> Optional[datetime]:
        if not self.starts_at:
            return None
        return datetime.fromtimestamp(self.starts_at, tz=timezone.utc)

    @property
    def reminder_offsets_tuple(self) -> tuple[int, ...]:
        return _parse_offsets(self.reminder_offsets)


@dataclass(slots=True)
class Signup:
    raid_id: int
    user_id: int
    role_name: str
    created_at: int


@dataclass(slots=True)
class Reminder:
    raid_id: int
    remind_at: int
    offset: int
    sent: bool


@dataclass(slots=True)
class RaidSchedule:
    id: int
    guild_id: int
    channel_id: int
    template_id: Optional[int]
    name_pattern: str
    comment: str
    max_participants: int
    roles_json: str
    weekday: int
    time_of_day: str
    interval_days: int
    lead_time_hours: int
    reminder_offsets: str
    next_run_at: int
    generate_at: int
    created_by: int

    @property
    def reminder_offsets_tuple(self) -> tuple[int, ...]:
        return _parse_offsets(self.reminder_offsets)

    @property
    def roles(self) -> Dict[str, int]:
        import json

        data = json.loads(self.roles_json) if self.roles_json else {}
        return {str(k): int(v) for k, v in data.items()}


@dataclass(slots=True)
class RaidTemplate:
    id: int
    guild_id: int
    name: str
    max_participants: int
    roles_json: str
    comment: str
    reminder_offsets: str

    @property
    def roles(self) -> Dict[str, int]:
        import json

        data = json.loads(self.roles_json) if self.roles_json else {}
        return {str(k): int(v) for k, v in data.items()}

    @property
    def reminder_offsets_tuple(self) -> tuple[int, ...]:
        return _parse_offsets(self.reminder_offsets)


__all__ = [
    "Raid",
    "RaidSchedule",
    "Reminder",
    "RaidTemplate",
    "Signup",
    "WaitlistEntry",
]

"""Domain models used by the raid bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional


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

    @property
    def starts_dt(self) -> Optional[datetime]:
        if not self.starts_at:
            return None
        return datetime.fromtimestamp(self.starts_at, tz=timezone.utc)


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
class RaidTemplate:
    id: int
    guild_id: int
    name: str
    max_participants: int
    roles_json: str
    comment: str

    @property
    def roles(self) -> Dict[str, int]:
        import json

        data = json.loads(self.roles_json) if self.roles_json else {}
        return {str(k): int(v) for k, v in data.items()}


__all__ = ["Raid", "Reminder", "RaidTemplate", "Signup", "WaitlistEntry"]

"""Domain models used by the raid bot."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


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


__all__ = ["Raid", "Signup"]

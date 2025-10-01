"""Business logic helpers for the raid bot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Mapping, Sequence, Tuple

from config import TIME_FMT
from models import Raid, Signup, WaitlistEntry

if TYPE_CHECKING:
    import discord


def parse_roles(roles_str: str) -> Mapping[str, int]:
    if not roles_str:
        return {}
    result: dict[str, int] = {}
    for chunk in roles_str.split(","):
        part = chunk.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Invalid role chunk '{part}'. Use name:count")
        name, count = part.split(":", 1)
        name = name.strip()
        try:
            capacity = int(count.strip())
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid count for role '{name}': '{count}'") from exc
        if capacity < 0:
            raise ValueError(f"Role capacity must be >= 0 for '{name}'")
        result[name] = capacity
    if not result:
        raise ValueError("At least one role must be specified")
    return result


def parse_time_local(value: str) -> datetime:
    naive = datetime.strptime(value, TIME_FMT)
    local_ts = naive.timestamp()
    return datetime.fromtimestamp(local_ts, tz=timezone.utc)


def parse_time_of_day(value: str) -> Tuple[int, int]:
    try:
        hour_str, minute_str = value.split(":", 1)
    except ValueError as exc:
        raise ValueError("Время должно быть в формате ЧЧ:ММ") from exc
    try:
        hour = int(hour_str)
        minute = int(minute_str)
    except ValueError as exc:
        raise ValueError("Часы и минуты должны быть числами") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Часы 0-23, минуты 0-59")
    return hour, minute


def compute_next_occurrence(
    weekday: int, hour: int, minute: int, *, now: datetime | None = None
) -> datetime:
    if not (0 <= weekday <= 6):
        raise ValueError("День недели должен быть от 0 до 6")
    base = now or datetime.now(tz=timezone.utc)
    local_now = base.astimezone()
    days_ahead = (weekday - local_now.weekday()) % 7
    candidate_date = (local_now + timedelta(days=days_ahead)).date()
    candidate = datetime(
        candidate_date.year,
        candidate_date.month,
        candidate_date.day,
        hour,
        minute,
        tzinfo=local_now.tzinfo,
    )
    if candidate <= local_now:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def parse_reminder_offsets(value: str | None) -> Tuple[int, ...]:
    if not value:
        return ()
    parts: list[int] = []
    for chunk in value.split(","):
        raw = chunk.strip().lower()
        if not raw:
            continue
        multiplier = 60
        if raw.endswith("h"):
            multiplier = 3600
            raw = raw[:-1]
        elif raw.endswith("m"):
            multiplier = 60
            raw = raw[:-1]
        elif raw.endswith("d"):
            multiplier = 86400
            raw = raw[:-1]
        try:
            amount = int(raw)
        except ValueError as exc:
            raise ValueError(
                "Интервалы напоминаний указываются числами (например 60, 30m, 2h)"
            ) from exc
        if amount <= 0:
            raise ValueError("Интервалы должны быть положительными")
        parts.append(amount * multiplier)
    if not parts:
        return ()
    return tuple(parts)


def ensure_permissions(interaction: "discord.Interaction", raid: Raid) -> bool:
    """Return True if the user is allowed to manage the raid."""
    if interaction.user.id == raid.created_by:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and (getattr(perms, "manage_events", False) or getattr(perms, "manage_guild", False)):
        return True
    return False


def build_roster_text(roles: Mapping[str, int], signups: Sequence[Signup]) -> Tuple[str, int]:
    by_role: dict[str, list[int]] = {name: [] for name in roles}
    for signup in signups:
        by_role.setdefault(signup.role_name, []).append(signup.user_id)
    lines: list[str] = []
    total = 0
    for role_name, capacity in roles.items():
        members = by_role.get(role_name, [])
        total += len(members)
        user_tags = [f"<@{user_id}>" for user_id in members]
        bar = f"[{len(members)}/{capacity}]"
        lines.append(
            f"**{role_name}** {bar}: " + (", ".join(user_tags) if user_tags else "—")
        )
    return "\n".join(lines), total


def build_waitlist_text(roles: Mapping[str, int], waitlist: Sequence[WaitlistEntry]) -> str:
    if not waitlist:
        return ""
    by_role: dict[str, list[int]] = {}
    for entry in waitlist:
        by_role.setdefault(entry.role_name, []).append(entry.user_id)
    lines: list[str] = []
    for role_name in roles:
        members = by_role.get(role_name)
        if not members:
            continue
        mentions = ", ".join(f"<@{user_id}>" for user_id in members)
        lines.append(f"**{role_name}**: {mentions}")
    for role_name, members in by_role.items():
        if role_name in roles:
            continue
        mentions = ", ".join(f"<@{user_id}>" for user_id in members)
        lines.append(f"**{role_name}**: {mentions} (ожидает роли)")
    return "\n".join(lines)


def make_embed(
    raid: Raid,
    roles: Mapping[str, int],
    signups: Sequence[Signup],
    waitlist: Sequence[WaitlistEntry],
) -> "discord.Embed":
    import discord
    roster_text, total = build_roster_text(roles, signups)
    waitlist_text = build_waitlist_text(roles, waitlist)
    starts_dt = raid.starts_dt
    if starts_dt:
        starts_dt_local = starts_dt.astimezone()
        start_value = f"{starts_dt_local.strftime(TIME_FMT)} (локальное время сервера)"
    else:
        start_value = "Не указано"

    embed = discord.Embed(title=f"🎯 {raid.name}", color=discord.Color.blurple())
    embed.add_field(name="Старт", value=start_value)
    embed.add_field(name="Лимит", value=f"{total}/{raid.max_participants}")
    if raid.comment:
        embed.add_field(name="Комментарий", value=raid.comment, inline=False)
    embed.add_field(name="Состав", value=roster_text or "—", inline=False)
    if waitlist_text:
        embed.add_field(name="Резерв", value=waitlist_text, inline=False)
    embed.set_footer(text=f"ID события: {raid.id}")
    return embed


__all__ = [
    "build_waitlist_text",
    "build_roster_text",
    "compute_next_occurrence",
    "ensure_permissions",
    "make_embed",
    "parse_reminder_offsets",
    "parse_roles",
    "parse_time_local",
    "parse_time_of_day",
]

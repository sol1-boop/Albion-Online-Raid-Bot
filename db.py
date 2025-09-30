"""Database access layer for the raid bot."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from config import DB_PATH
from models import Raid, Reminder, RaidTemplate, Signup, WaitlistEntry

DEFAULT_REMINDER_OFFSETS = (3600, 900, 300)


def with_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with with_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER,
                name TEXT NOT NULL,
                starts_at INTEGER NOT NULL,
                comment TEXT,
                max_participants INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raid_roles (
                raid_id INTEGER NOT NULL,
                role_name TEXT NOT NULL,
                capacity INTEGER NOT NULL,
                PRIMARY KEY (raid_id, role_name),
                FOREIGN KEY (raid_id) REFERENCES raids(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raid_signups (
                raid_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (raid_id, user_id),
                FOREIGN KEY (raid_id) REFERENCES raids(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raid_waitlist (
                raid_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                role_name TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (raid_id, user_id),
                FOREIGN KEY (raid_id) REFERENCES raids(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raid_reminders (
                raid_id INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                remind_at INTEGER NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (raid_id, offset),
                FOREIGN KEY (raid_id) REFERENCES raids(id) ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS raid_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                max_participants INTEGER NOT NULL,
                roles_json TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                UNIQUE (guild_id, name)
            )
            """
        )
        conn.commit()


def create_raid(
    *,
    guild_id: int,
    channel_id: int,
    name: str,
    starts_at: int,
    comment: str,
    max_participants: int,
    created_by: int,
    roles: Dict[str, int],
) -> int:
    with with_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO raids (
                guild_id,
                channel_id,
                name,
                starts_at,
                comment,
                max_participants,
                created_by,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                channel_id,
                name,
                starts_at,
                comment,
                max_participants,
                created_by,
                int(datetime.now(tz=timezone.utc).timestamp()),
            ),
        )
        raid_id = int(cur.lastrowid)
        for role_name, capacity in roles.items():
            cur.execute(
                "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                (raid_id, role_name, int(capacity)),
            )
        conn.commit()
    return raid_id


def update_raid(
    raid_id: int,
    *,
    name: Optional[str] = None,
    starts_at: Optional[int] = None,
    max_participants: Optional[int] = None,
    comment: Optional[str] = None,
) -> None:
    fields: List[str] = []
    params: List[object] = []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if starts_at is not None:
        fields.append("starts_at = ?")
        params.append(starts_at)
    if max_participants is not None:
        fields.append("max_participants = ?")
        params.append(max_participants)
    if comment is not None:
        fields.append("comment = ?")
        params.append(comment)
    if not fields:
        return
    with with_conn() as conn:
        conn.execute(
            f"UPDATE raids SET {', '.join(fields)} WHERE id = ?",
            (*params, raid_id),
        )
        conn.commit()


def replace_roles(raid_id: int, roles: Dict[str, int]) -> None:
    with with_conn() as conn:
        conn.execute("DELETE FROM raid_roles WHERE raid_id = ?", (raid_id,))
        for role_name, capacity in roles.items():
            conn.execute(
                "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                (raid_id, role_name, int(capacity)),
            )
        conn.execute(
            "DELETE FROM raid_signups WHERE raid_id = ? AND role_name NOT IN ("
            "SELECT role_name FROM raid_roles WHERE raid_id = ?"
            ")",
            (raid_id, raid_id),
        )
        conn.execute(
            "DELETE FROM raid_waitlist WHERE raid_id = ? AND role_name NOT IN ("
            "SELECT role_name FROM raid_roles WHERE raid_id = ?"
            ")",
            (raid_id, raid_id),
        )
        conn.commit()


def delete_raid(raid_id: int) -> None:
    with with_conn() as conn:
        conn.execute("DELETE FROM raids WHERE id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_roles WHERE raid_id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_signups WHERE raid_id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_waitlist WHERE raid_id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_reminders WHERE raid_id = ?", (raid_id,))
        conn.commit()


def update_message_id(raid_id: int, message_id: int) -> None:
    with with_conn() as conn:
        conn.execute("UPDATE raids SET message_id = ? WHERE id = ?", (message_id, raid_id))
        conn.commit()


def fetch_raid(raid_id: int) -> Optional[Raid]:
    with with_conn() as conn:
        row = conn.execute("SELECT * FROM raids WHERE id = ?", (raid_id,)).fetchone()
    if not row:
        return None
    return Raid(**dict(row))


def get_roles(raid_id: int) -> Dict[str, int]:
    with with_conn() as conn:
        rows = conn.execute(
            "SELECT role_name, capacity FROM raid_roles WHERE raid_id = ? ORDER BY role_name",
            (raid_id,),
        ).fetchall()
    return {str(row["role_name"]): int(row["capacity"]) for row in rows}


def get_signups(raid_id: int) -> List[Signup]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT raid_id, user_id, role_name, created_at
            FROM raid_signups
            WHERE raid_id = ?
            ORDER BY created_at
            """,
            (raid_id,),
        ).fetchall()
    return [
        Signup(
            raid_id=int(row["raid_id"]),
            user_id=int(row["user_id"]),
            role_name=str(row["role_name"]),
            created_at=int(row["created_at"]),
        )
        for row in rows
    ]


def get_waitlist(raid_id: int) -> List[WaitlistEntry]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT raid_id, user_id, role_name, created_at
            FROM raid_waitlist
            WHERE raid_id = ?
            ORDER BY created_at
            """,
            (raid_id,),
        ).fetchall()
    return [
        WaitlistEntry(
            raid_id=int(row["raid_id"]),
            user_id=int(row["user_id"]),
            role_name=str(row["role_name"]),
            created_at=int(row["created_at"]),
        )
        for row in rows
    ]


def get_user_signup(raid_id: int, user_id: int) -> Optional[Signup]:
    with with_conn() as conn:
        row = conn.execute(
            """
            SELECT raid_id, user_id, role_name, created_at
            FROM raid_signups
            WHERE raid_id = ? AND user_id = ?
            """,
            (raid_id, user_id),
        ).fetchone()
    if not row:
        return None
    return Signup(
        raid_id=int(row["raid_id"]),
        user_id=int(row["user_id"]),
        role_name=str(row["role_name"]),
        created_at=int(row["created_at"]),
    )


def get_waitlist_entry(raid_id: int, user_id: int) -> Optional[WaitlistEntry]:
    with with_conn() as conn:
        row = conn.execute(
            """
            SELECT raid_id, user_id, role_name, created_at
            FROM raid_waitlist
            WHERE raid_id = ? AND user_id = ?
            """,
            (raid_id, user_id),
        ).fetchone()
    if not row:
        return None
    return WaitlistEntry(
        raid_id=int(row["raid_id"]),
        user_id=int(row["user_id"]),
        role_name=str(row["role_name"]),
        created_at=int(row["created_at"]),
    )


def add_signup(raid_id: int, user_id: int, role_name: str, created_at: Optional[int] = None) -> None:
    created_ts = created_at or int(datetime.now(tz=timezone.utc).timestamp())
    with with_conn() as conn:
        conn.execute(
            """
            INSERT INTO raid_signups (raid_id, user_id, role_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (raid_id, user_id, role_name, created_ts),
        )
        conn.commit()


def add_waitlist_entry(
    raid_id: int, user_id: int, role_name: str, created_at: Optional[int] = None
) -> None:
    created_ts = created_at or int(datetime.now(tz=timezone.utc).timestamp())
    existing = get_waitlist_entry(raid_id, user_id)
    if existing:
        with with_conn() as conn:
            conn.execute(
                "UPDATE raid_waitlist SET role_name = ?, created_at = ? WHERE raid_id = ? AND user_id = ?",
                (role_name, min(existing.created_at, created_ts), raid_id, user_id),
            )
            conn.commit()
    else:
        with with_conn() as conn:
            conn.execute(
                """
                INSERT INTO raid_waitlist (raid_id, user_id, role_name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (raid_id, user_id, role_name, created_ts),
            )
            conn.commit()


def update_waitlist_role(raid_id: int, user_id: int, role_name: str) -> None:
    with with_conn() as conn:
        conn.execute(
            "UPDATE raid_waitlist SET role_name = ? WHERE raid_id = ? AND user_id = ?",
            (role_name, raid_id, user_id),
        )
        conn.commit()


def update_signup_role(raid_id: int, user_id: int, role_name: str) -> None:
    with with_conn() as conn:
        conn.execute(
            "UPDATE raid_signups SET role_name = ? WHERE raid_id = ? AND user_id = ?",
            (role_name, raid_id, user_id),
        )
        conn.commit()


def remove_signup(raid_id: int, user_id: int) -> None:
    with with_conn() as conn:
        conn.execute(
            "DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?",
            (raid_id, user_id),
        )
        conn.commit()


def remove_waitlist_entry(raid_id: int, user_id: int) -> None:
    with with_conn() as conn:
        conn.execute(
            "DELETE FROM raid_waitlist WHERE raid_id = ? AND user_id = ?",
            (raid_id, user_id),
        )
        conn.commit()


def enforce_signup_limits(raid_id: int) -> Dict[str, List[Tuple[int, str]]]:
    raid = fetch_raid(raid_id)
    if not raid:
        return {"waitlisted": [], "removed": []}
    roles = get_roles(raid_id)
    signups = get_signups(raid_id)
    to_waitlist: List[Signup] = []
    to_remove: List[Signup] = []
    removed_ids: set[int] = set()

    for role_name, capacity in roles.items():
        role_signups = [s for s in signups if s.role_name == role_name and s.user_id not in removed_ids]
        if len(role_signups) > int(capacity):
            overflow = role_signups[int(capacity) :]
            to_waitlist.extend(overflow)
            removed_ids.update(s.user_id for s in overflow)

    for signup in signups:
        if signup.user_id in removed_ids:
            continue
        if signup.role_name not in roles:
            to_remove.append(signup)
            removed_ids.add(signup.user_id)

    remaining = [s for s in signups if s.user_id not in removed_ids]
    if len(remaining) > raid.max_participants:
        overflow = remaining[raid.max_participants :]
        to_waitlist.extend(overflow)
        removed_ids.update(s.user_id for s in overflow)

    if not to_waitlist and not to_remove:
        return {"waitlisted": [], "removed": []}

    with with_conn() as conn:
        if to_waitlist:
            conn.executemany(
                "DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?",
                [(raid_id, signup.user_id) for signup in to_waitlist],
            )
        if to_remove:
            conn.executemany(
                "DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?",
                [(raid_id, signup.user_id) for signup in to_remove],
            )
        conn.commit()

    for signup in to_waitlist:
        if signup.role_name in roles and roles[signup.role_name] > 0:
            add_waitlist_entry(raid_id, signup.user_id, signup.role_name, signup.created_at)
        else:
            to_remove.append(signup)

    return {
        "waitlisted": [(signup.user_id, signup.role_name) for signup in to_waitlist if signup.role_name in roles],
        "removed": [(signup.user_id, signup.role_name) for signup in to_remove if signup.role_name not in roles],
    }


def list_raid_ids() -> List[int]:
    with with_conn() as conn:
        rows = conn.execute("SELECT id FROM raids").fetchall()
    return [int(row["id"]) for row in rows]


def promote_waitlist(raid_id: int) -> List[Tuple[int, str]]:
    raid = fetch_raid(raid_id)
    if not raid:
        return []
    roles = get_roles(raid_id)
    if not roles:
        return []
    signups = get_signups(raid_id)
    waitlist = get_waitlist(raid_id)
    promoted: List[Tuple[int, str]] = []

    counts: Dict[str, int] = {name: 0 for name in roles}
    for signup in signups:
        if signup.role_name in counts:
            counts[signup.role_name] += 1
    total = len(signups)

    for entry in waitlist:
        if entry.role_name not in roles or roles[entry.role_name] <= 0:
            remove_waitlist_entry(raid_id, entry.user_id)
            continue
        if total >= raid.max_participants:
            break
        if counts[entry.role_name] >= roles[entry.role_name]:
            continue
        remove_waitlist_entry(raid_id, entry.user_id)
        add_signup(raid_id, entry.user_id, entry.role_name, entry.created_at)
        counts[entry.role_name] += 1
        total += 1
        promoted.append((entry.user_id, entry.role_name))

    return promoted


def list_upcoming_raids(guild_id: int, now_ts: int, limit: int) -> Sequence[Raid]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM raids
            WHERE guild_id = ?
              AND (starts_at = 0 OR starts_at >= ?)
            ORDER BY CASE WHEN starts_at = 0 THEN 0 ELSE 1 END, starts_at
            LIMIT ?
            """,
            (guild_id, now_ts, limit),
        ).fetchall()
    return [Raid(**dict(row)) for row in rows]


def reset_raid_reminders(raid_id: int, starts_at: int, offsets: Sequence[int] | None = None) -> None:
    offsets = tuple(offsets or DEFAULT_REMINDER_OFFSETS)
    with with_conn() as conn:
        conn.execute("DELETE FROM raid_reminders WHERE raid_id = ?", (raid_id,))
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        if starts_at and starts_at > now_ts:
            for offset in offsets:
                remind_at = starts_at - int(offset)
                if remind_at < now_ts:
                    remind_at = now_ts
                conn.execute(
                    """
                    INSERT INTO raid_reminders (raid_id, offset, remind_at, sent)
                    VALUES (?, ?, ?, 0)
                    """,
                    (raid_id, int(offset), remind_at),
                )
        conn.commit()


def delete_raid_reminders(raid_id: int) -> None:
    with with_conn() as conn:
        conn.execute("DELETE FROM raid_reminders WHERE raid_id = ?", (raid_id,))
        conn.commit()


def list_due_reminders(now_ts: int) -> List[Reminder]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT raid_id, remind_at, offset, sent
            FROM raid_reminders
            WHERE sent = 0 AND remind_at <= ?
            ORDER BY remind_at
            """,
            (now_ts,),
        ).fetchall()
    return [
        Reminder(
            raid_id=int(row["raid_id"]),
            remind_at=int(row["remind_at"]),
            offset=int(row["offset"]),
            sent=bool(row["sent"]),
        )
        for row in rows
    ]


def mark_reminder_sent(raid_id: int, offset: int) -> None:
    with with_conn() as conn:
        conn.execute(
            "UPDATE raid_reminders SET sent = 1 WHERE raid_id = ? AND offset = ?",
            (raid_id, offset),
        )
        conn.commit()


def list_reminders_for_raid(raid_id: int) -> List[Reminder]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT raid_id, remind_at, offset, sent
            FROM raid_reminders
            WHERE raid_id = ?
            ORDER BY remind_at
            """,
            (raid_id,),
        ).fetchall()
    return [
        Reminder(
            raid_id=int(row["raid_id"]),
            remind_at=int(row["remind_at"]),
            offset=int(row["offset"]),
            sent=bool(row["sent"]),
        )
        for row in rows
    ]


def save_template(
    guild_id: int,
    name: str,
    *,
    max_participants: int,
    roles: Dict[str, int],
    comment: str = "",
) -> int:
    import json

    roles_json = json.dumps({str(k): int(v) for k, v in roles.items()})
    with with_conn() as conn:
        conn.execute(
            """
            INSERT INTO raid_templates (guild_id, name, max_participants, roles_json, comment)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, name) DO UPDATE SET
                max_participants = excluded.max_participants,
                roles_json = excluded.roles_json,
                comment = excluded.comment
            """,
            (guild_id, name, int(max_participants), roles_json, comment or ""),
        )
        row = conn.execute(
            "SELECT id FROM raid_templates WHERE guild_id = ? AND name = ?",
            (guild_id, name),
        ).fetchone()
        conn.commit()
    if not row:
        raise RuntimeError("Failed to save template")
    return int(row["id"])


def delete_template(guild_id: int, name: str) -> bool:
    with with_conn() as conn:
        cur = conn.execute(
            "DELETE FROM raid_templates WHERE guild_id = ? AND name = ?",
            (guild_id, name),
        )
        conn.commit()
    return cur.rowcount > 0


def list_templates(guild_id: int) -> List[RaidTemplate]:
    with with_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, guild_id, name, max_participants, roles_json, comment
            FROM raid_templates
            WHERE guild_id = ?
            ORDER BY name
            """,
            (guild_id,),
        ).fetchall()
    return [RaidTemplate(**dict(row)) for row in rows]


def fetch_template(guild_id: int, name: str) -> Optional[RaidTemplate]:
    with with_conn() as conn:
        row = conn.execute(
            """
            SELECT id, guild_id, name, max_participants, roles_json, comment
            FROM raid_templates
            WHERE guild_id = ? AND name = ?
            """,
            (guild_id, name),
        ).fetchone()
    if not row:
        return None
    return RaidTemplate(**dict(row))


__all__ = [
    "DEFAULT_REMINDER_OFFSETS",
    "add_signup",
    "add_waitlist_entry",
    "create_raid",
    "delete_raid",
    "delete_raid_reminders",
    "delete_template",
    "enforce_signup_limits",
    "fetch_raid",
    "fetch_template",
    "get_roles",
    "get_signups",
    "get_user_signup",
    "get_waitlist",
    "get_waitlist_entry",
    "init_db",
    "list_due_reminders",
    "list_raid_ids",
    "list_reminders_for_raid",
    "list_templates",
    "list_upcoming_raids",
    "mark_reminder_sent",
    "promote_waitlist",
    "remove_signup",
    "remove_waitlist_entry",
    "replace_roles",
    "reset_raid_reminders",
    "save_template",
    "update_message_id",
    "update_raid",
    "update_signup_role",
    "update_waitlist_role",
    "with_conn",
]

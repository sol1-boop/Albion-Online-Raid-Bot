"""Database access layer for the raid bot."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from config import DB_PATH
from models import Raid, Signup


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
        conn.commit()


def delete_raid(raid_id: int) -> None:
    with with_conn() as conn:
        conn.execute("DELETE FROM raids WHERE id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_roles WHERE raid_id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_signups WHERE raid_id = ?", (raid_id,))
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


def enforce_signup_limits(raid_id: int) -> List[Tuple[int, str]]:
    raid = fetch_raid(raid_id)
    if not raid:
        return []
    roles = get_roles(raid_id)
    signups = get_signups(raid_id)
    to_remove: List[Signup] = []
    removed_ids: set[int] = set()

    for role_name, capacity in roles.items():
        role_signups = [s for s in signups if s.role_name == role_name and s.user_id not in removed_ids]
        if len(role_signups) > int(capacity):
            to_remove.extend(role_signups[int(capacity) :])
            removed_ids.update(s.user_id for s in role_signups[int(capacity) :])

    for signup in signups:
        if signup.user_id in removed_ids:
            continue
        if signup.role_name not in roles:
            to_remove.append(signup)
            removed_ids.add(signup.user_id)

    remaining = [s for s in signups if s.user_id not in removed_ids]
    if len(remaining) > raid.max_participants:
        overflow = remaining[raid.max_participants :]
        to_remove.extend(overflow)
        removed_ids.update(s.user_id for s in overflow)

    if not to_remove:
        return []

    with with_conn() as conn:
        conn.executemany(
            "DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?",
            [(raid_id, signup.user_id) for signup in to_remove],
        )
        conn.commit()

    return [(signup.user_id, signup.role_name) for signup in to_remove]


def list_raid_ids() -> List[int]:
    with with_conn() as conn:
        rows = conn.execute("SELECT id FROM raids").fetchall()
    return [int(row["id"]) for row in rows]


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


__all__ = [
    "add_signup",
    "create_raid",
    "delete_raid",
    "enforce_signup_limits",
    "fetch_raid",
    "get_roles",
    "get_signups",
    "get_user_signup",
    "init_db",
    "list_raid_ids",
    "list_upcoming_raids",
    "remove_signup",
    "replace_roles",
    "update_message_id",
    "update_raid",
    "update_signup_role",
    "with_conn",
]

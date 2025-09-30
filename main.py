import asyncio
import os
import logging
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ===================== Env & Config =====================
load_dotenv()  # Loads variables from .env if present

DB_PATH = os.getenv("RAIDBOT_DB", "raids.db")
TOKEN = os.getenv("DISCORD_TOKEN")
# Fallback for local testing without env vars
if not TOKEN and os.path.exists("token.txt"):
    with open("token.txt", "r", encoding="utf-8") as f:
        TOKEN = f.read().strip()

# Human-facing time format. Interpreted as *server local time* on input.
TIME_FMT = "%H:%M %d.%m.%Y"  # e.g. 22:00 30.09.2025

# ===================== Logging =====================
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("raidbot")

# ===================== Data layer =====================

def with_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with with_conn() as conn:
        c = conn.cursor()
        c.execute(
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
        c.execute(
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
        c.execute(
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


@dataclass
class Raid:
    id: int
    guild_id: int
    channel_id: int
    message_id: Optional[int]
    name: str
    starts_at: int  # epoch seconds UTC or 0 if не указано
    comment: str
    max_participants: int
    created_by: int
    created_at: int

    @property
    def starts_dt(self) -> Optional[datetime]:
        if not self.starts_at:
            return None
        return datetime.fromtimestamp(self.starts_at, tz=timezone.utc)


# ===================== Utilities =====================

def parse_roles(roles_str: str) -> Dict[str, int]:
    """Parse roles description like 'tank:2, healer:3, dps:10' -> dict.
    Whitespace is allowed. Role names are case-insensitive but stored as given.
    """
    if not roles_str:
        return {}
    result: Dict[str, int] = {}
    for chunk in roles_str.split(','):
        part = chunk.strip()
        if not part:
            continue
        if ':' not in part:
            raise ValueError(f"Invalid role chunk '{part}'. Use name:count")
        name, count = part.split(':', 1)
        name = name.strip()
        try:
            c = int(count.strip())
        except ValueError:
            raise ValueError(f"Invalid count for role '{name}': '{count}'")
        if c < 0:
            raise ValueError(f"Role capacity must be >= 0 for '{name}'")
        result[name] = c
    if not result:
        raise ValueError("At least one role must be specified")
    return result


def parse_time_local(s: str) -> datetime:
    """Parse local time in TIME_FMT and convert to UTC aware dt."""
    naive = datetime.strptime(s, TIME_FMT)
    # Interpret as local server time; convert to UTC
    local_ts = naive.timestamp()  # uses local timezone of the machine
    return datetime.fromtimestamp(local_ts, tz=timezone.utc)


async def ensure_permissions(interaction: discord.Interaction, raid: Raid) -> bool:
    """Only raid creator or users with Manage Events can edit/delete."""
    if interaction.user.id == raid.created_by:
        return True
    perms = interaction.user.guild_permissions
    if getattr(perms, 'manage_events', False) or getattr(perms, 'manage_guild', False):
        return True
    await interaction.response.send_message(
        "Недостаточно прав: только создатель события или модератор с Manage Events.",
        ephemeral=True,
    )
    return False


# ===================== Discord Bot =====================

intents = discord.Intents.default()
intents.message_content = False
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

group = app_commands.Group(name="raid", description="Рейдовые события Albion Online")


async def fetch_raid(raid_id: int) -> Optional[Raid]:
    with with_conn() as conn:
        r = conn.execute("SELECT * FROM raids WHERE id = ?", (raid_id,)).fetchone()
        if not r:
            return None
        return Raid(**dict(r))


def get_roles(raid_id: int) -> Dict[str, int]:
    with with_conn() as conn:
        rows = conn.execute(
            "SELECT role_name, capacity FROM raid_roles WHERE raid_id = ? ORDER BY role_name",
            (raid_id,),
        ).fetchall()
        return {row[0]: row[1] for row in rows}


def get_signups(raid_id: int) -> List[sqlite3.Row]:
    with with_conn() as conn:
        return conn.execute(
            "SELECT user_id, role_name, created_at FROM raid_signups WHERE raid_id = ? ORDER BY created_at",
            (raid_id,),
        ).fetchall()


def get_user_signup(raid_id: int, user_id: int) -> Optional[sqlite3.Row]:
    with with_conn() as conn:
        return conn.execute(
            "SELECT role_name, created_at FROM raid_signups WHERE raid_id = ? AND user_id = ?",
            (raid_id, user_id),
        ).fetchone()


def enforce_signup_limits(raid_id: int) -> List[Tuple[int, str]]:
    """Ensure signups obey current raid limits, trimming the latest entries if needed."""
    with with_conn() as conn:
        raid_row = conn.execute(
            "SELECT max_participants FROM raids WHERE id = ?",
            (raid_id,),
        ).fetchone()
    if not raid_row:
        return []

    max_participants = int(raid_row["max_participants"])
    roles = get_roles(raid_id)
    signups_rows = get_signups(raid_id)
    signups = [
        {
            "user_id": row["user_id"],
            "role_name": row["role_name"],
            "created_at": row["created_at"],
        }
        for row in signups_rows
    ]
    to_remove: List[Dict[str, object]] = []
    removed_ids = set()

    # Enforce per-role capacities (keep earliest signups)
    for role_name, cap in roles.items():
        cap_int = int(cap)
        role_signups = [s for s in signups if s["role_name"] == role_name and s["user_id"] not in removed_ids]
        if len(role_signups) > cap_int:
            for entry in role_signups[cap_int:]:
                if entry["user_id"] not in removed_ids:
                    to_remove.append(entry)
                    removed_ids.add(entry["user_id"])

    # Any signups for roles not present anymore should also be removed
    for entry in signups:
        if entry["user_id"] in removed_ids:
            continue
        if entry["role_name"] not in roles:
            to_remove.append(entry)
            removed_ids.add(entry["user_id"])

    # Enforce total capacity (keep earliest signups)
    remaining = [s for s in signups if s["user_id"] not in removed_ids]
    if len(remaining) > max_participants:
        overflow = remaining[max_participants:]
        for entry in overflow:
            if entry["user_id"] not in removed_ids:
                to_remove.append(entry)
                removed_ids.add(entry["user_id"])

    if not to_remove:
        return []

    with with_conn() as conn:
        conn.executemany(
            "DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?",
            [(raid_id, int(entry["user_id"])) for entry in to_remove],
        )
        conn.commit()

    return [(int(entry["user_id"]), str(entry["role_name"])) for entry in to_remove]


def build_roster_text(raid_id: int) -> Tuple[str, int]:
    roles = get_roles(raid_id)
    signups = get_signups(raid_id)
    by_role: Dict[str, List[int]] = {name: [] for name in roles.keys()}
    for row in signups:
        by_role.setdefault(row[1], []).append(row[0])

    lines: List[str] = []
    total = 0
    for role_name, cap in roles.items():
        members = by_role.get(role_name, [])
        total += len(members)
        user_tags = [f"<@{uid}>" for uid in members]
        bar = f"[{len(members)}/{cap}]"
        lines.append(f"**{role_name}** {bar}: " + (", ".join(user_tags) if user_tags else "—"))
    return ("\n".join(lines), total)


def make_embed(raid: Raid) -> discord.Embed:
    roster_text, total = build_roster_text(raid.id)
    starts_dt = raid.starts_dt
    if starts_dt:
        starts_dt_local = starts_dt.astimezone()  # server local timezone for display
        start_value = f"{starts_dt_local.strftime(TIME_FMT)} (локальное время сервера)"
    else:
        start_value = "Не указано"

    e = discord.Embed(title=f"🎯 {raid.name}", color=discord.Color.blurple())
    e.add_field(name="Старт", value=start_value)
    e.add_field(name="Лимит", value=f"{total}/{raid.max_participants}")
    if raid.comment:
        e.add_field(name="Комментарий", value=raid.comment, inline=False)
    e.add_field(name="Состав", value=roster_text or "—", inline=False)
    e.set_footer(text=f"ID события: {raid.id}")
    return e


class SignupView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=None)
        self.raid_id = raid_id
        # Dynamic select with roles
        options = [
            discord.SelectOption(label=name, description=f"Лимит {cap}")
            for name, cap in get_roles(raid_id).items()
        ]
        self.add_item(RoleSelect(raid_id, options))
        self.add_item(LeaveButton(raid_id))


class RoleSelect(discord.ui.Select):
    def __init__(self, raid_id: int, options: List[discord.SelectOption]):
        super().__init__(placeholder="Выберите роль и запишитесь", min_values=1, max_values=1, options=options)
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await handle_signup(interaction, self.raid_id, role)


class LeaveButton(discord.ui.Button):
    def __init__(self, raid_id: int):
        super().__init__(label="Снять запись", style=discord.ButtonStyle.secondary)
        self.raid_id = raid_id

    async def callback(self, interaction: discord.Interaction):
        await handle_unsubscribe(interaction, self.raid_id)


async def handle_signup(interaction: discord.Interaction, raid_id: int, role_name: str):
    raid = await fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return

    # capacity checks
    roles = get_roles(raid_id)
    if role_name not in roles:
        await interaction.response.send_message("Такой роли нет в этом событии.", ephemeral=True)
        return

    current_signup = get_user_signup(raid_id, interaction.user.id)
    signups = get_signups(raid_id)
    signups_without_user = [row for row in signups if row["user_id"] != interaction.user.id]

    total_after = len(signups_without_user) + 1
    if total_after > raid.max_participants:
        await interaction.response.send_message("Лимит участников достигнут.", ephemeral=True)
        return

    role_after = len([1 for row in signups_without_user if row["role_name"] == role_name]) + 1
    if role_after > roles[role_name]:
        await interaction.response.send_message("Лимит по этой роли достигнут.", ephemeral=True)
        return

    with with_conn() as conn:
        if current_signup:
            conn.execute(
                "UPDATE raid_signups SET role_name = ? WHERE raid_id = ? AND user_id = ?",
                (role_name, raid_id, interaction.user.id),
            )
        else:
            conn.execute(
                "INSERT INTO raid_signups (raid_id, user_id, role_name, created_at) VALUES (?, ?, ?, ?)",
                (
                    raid_id,
                    interaction.user.id,
                    role_name,
                    int(datetime.now(tz=timezone.utc).timestamp()),
                ),
            )
        conn.commit()

    # Update message embed if present
    await refresh_message(interaction.client, raid)
    await interaction.response.send_message(f"Вы записались как **{role_name}**.", ephemeral=True)


async def handle_unsubscribe(interaction: discord.Interaction, raid_id: int):
    raid = await fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    with with_conn() as conn:
        conn.execute("DELETE FROM raid_signups WHERE raid_id = ? AND user_id = ?", (raid_id, interaction.user.id))
        conn.commit()
    await refresh_message(interaction.client, raid)
    await interaction.response.send_message("Запись снята.", ephemeral=True)


async def refresh_message(client: discord.Client, raid: Raid):
    if not raid.message_id:
        return
    channel = client.get_channel(raid.channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    try:
        msg = await channel.fetch_message(raid.message_id)
    except discord.NotFound:
        return
    await msg.edit(embed=make_embed(raid), view=SignupView(raid.id))


# ===================== Slash commands =====================

@group.command(name="create", description="Создать рейдовое событие")
@app_commands.describe(
    name="Название события",
    starts_at=f"Время старта в формате {TIME_FMT} (локальное время сервера, опционально)",
    max_participants="Общий лимит участников",
    roles="Роли и лимиты в формате: tank:2, healer:3, dps:10",
    comment="Комментарий (опционально)",
)
async def raid_create(
    interaction: discord.Interaction,
    name: str,
    max_participants: app_commands.Range[int, 1, 1000],
    roles: str,
    starts_at: Optional[str] = None,
    comment: Optional[str] = None,
):
    dt_utc: Optional[datetime] = None
    try:
        if starts_at:
            dt_utc = parse_time_local(starts_at)
        roles_map = parse_roles(roles)
    except Exception as e:
        await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)
        return

    with with_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO raids (guild_id, channel_id, name, starts_at, comment, max_participants, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                interaction.guild_id,
                interaction.channel_id,
                name,
                int(dt_utc.timestamp()) if dt_utc else 0,
                comment or "",
                int(max_participants),
                interaction.user.id,
                int(datetime.now(tz=timezone.utc).timestamp()),
            ),
        )
        raid_id = cur.lastrowid
        for rname, cap in roles_map.items():
            cur.execute(
                "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                (raid_id, rname, cap),
            )
        conn.commit()

    raid = await fetch_raid(raid_id)
    assert raid

    embed = make_embed(raid)
    view = SignupView(raid.id)
    await interaction.response.send_message(embed=embed, view=view)
    msg = await interaction.original_response()

    with with_conn() as conn:
        conn.execute("UPDATE raids SET message_id = ? WHERE id = ?", (msg.id, raid_id))
        conn.commit()


@group.command(name="edit", description="Редактировать событие")
@app_commands.describe(
    raid_id="ID события",
    name="Новое название (опционально)",
    starts_at=f"Новое время {TIME_FMT} (опционально)",
    max_participants="Новый общий лимит (опционально)",
    roles="Полный список ролей и лимитов (заменит существующие)",
    comment="Новый комментарий (опционально)",
)
async def raid_edit(
    interaction: discord.Interaction,
    raid_id: int,
    name: Optional[str] = None,
    starts_at: Optional[str] = None,
    max_participants: Optional[int] = None,
    roles: Optional[str] = None,
    comment: Optional[str] = None,
):
    raid = await fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    if not await ensure_permissions(interaction, raid):
        return

    updates = []
    params: List[object] = []

    if name:
        updates.append("name = ?")
        params.append(name)
    if starts_at:
        try:
            dt_utc = parse_time_local(starts_at)
        except Exception as e:
            await interaction.response.send_message(f"Ошибка времени: {e}", ephemeral=True)
            return
        updates.append("starts_at = ?")
        params.append(int(dt_utc.timestamp()))
    if max_participants is not None:
        updates.append("max_participants = ?")
        params.append(int(max_participants))
    if comment is not None:
        updates.append("comment = ?")
        params.append(comment)

    if updates:
        with with_conn() as conn:
            conn.execute(f"UPDATE raids SET {', '.join(updates)} WHERE id = ?", (*params, raid_id))
            conn.commit()

    if roles is not None:
        try:
            new_roles = parse_roles(roles)
        except Exception as e:
            await interaction.response.send_message(f"Ошибка ролей: {e}", ephemeral=True)
            return
        with with_conn() as conn:
            conn.execute("DELETE FROM raid_roles WHERE raid_id = ?", (raid_id,))
            for rname, cap in new_roles.items():
                conn.execute(
                    "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                    (raid_id, rname, cap),
                )
            # Drop signups for roles that no longer exist
            conn.execute(
                "DELETE FROM raid_signups WHERE raid_id = ? AND role_name NOT IN (SELECT role_name FROM raid_roles WHERE raid_id = ?)",
                (raid_id, raid_id),
            )
            conn.commit()

    removed_signups = enforce_signup_limits(raid_id)

    raid = await fetch_raid(raid_id)
    if raid:
        await refresh_message(interaction.client, raid)  # type: ignore[arg-type]

    if removed_signups:
        removed_text = ", ".join(f"<@{uid}> ({role})" for uid, role in removed_signups)
        message = "Событие обновлено. Сняты из-за новых лимитов: " + removed_text
    else:
        message = "Событие обновлено."
    await interaction.response.send_message(message, ephemeral=True)


@group.command(name="delete", description="Удалить событие")
@app_commands.describe(raid_id="ID события для удаления")
async def raid_delete(interaction: discord.Interaction, raid_id: int):
    raid = await fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    if not await ensure_permissions(interaction, raid):
        return
    with with_conn() as conn:
        conn.execute("DELETE FROM raids WHERE id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_roles WHERE raid_id = ?", (raid_id,))
        conn.execute("DELETE FROM raid_signups WHERE raid_id = ?", (raid_id,))
        conn.commit()
    await interaction.response.send_message("Событие удалено.", ephemeral=True)
    # Try to edit message to show deletion
    if raid.message_id:
        channel = interaction.client.get_channel(raid.channel_id)
        try:
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                msg = await channel.fetch_message(raid.message_id)
                await msg.edit(content="(Событие удалено)", embed=None, view=None)
        except Exception:
            pass


@group.command(name="view", description="Показать событие")
@app_commands.describe(raid_id="ID события")
async def raid_view(interaction: discord.Interaction, raid_id: int):
    raid = await fetch_raid(raid_id)
    if not raid:
        await interaction.response.send_message("Событие не найдено.", ephemeral=True)
        return
    await interaction.response.send_message(embed=make_embed(raid), view=SignupView(raid.id))


@group.command(name="list", description="Список ближайших событий")
@app_commands.describe(limit="Сколько показать (по умолчанию 10)")
async def raid_list(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 1, 25] = 10,
):
    now = int(datetime.now(tz=timezone.utc).timestamp())
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
            (interaction.guild_id, now, int(limit)),
        ).fetchall()
    if not rows:
        await interaction.response.send_message("Нет ближайших событий.", ephemeral=True)
        return
    lines = []
    for r in rows:
        if r["starts_at"]:
            dt = datetime.fromtimestamp(r["starts_at"], tz=timezone.utc).astimezone()
            when = dt.strftime(TIME_FMT)
        else:
            when = "Без даты"
        lines.append(f"`{r['id']}` • {when} • {r['name']}")
    await interaction.response.send_message("\n".join(lines))


# ===================== Bot lifecycle =====================

@bot.event
async def on_ready():
    init_db()
    try:
        bot.tree.add_command(group)
    except Exception:
        pass

    await bot.tree.sync()
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    # Re-register persistent views for existing raid messages
    with with_conn() as conn:
        for row in conn.execute("SELECT id FROM raids").fetchall():
            bot.add_view(SignupView(row[0]))


# ===================== Minimal self-tests =====================

def _selftest() -> None:
    """Minimal self-tests for helpers. Run with RAIDBOT_SELFTEST=1."""
    # parse_roles happy path
    assert parse_roles("tank:2, healer:3, dps:10") == {"tank": 2, "healer": 3, "dps": 10}
    # parse_roles errors
    try:
        parse_roles("badchunk")
        raise AssertionError("expected ValueError for missing colon")
    except ValueError:
        pass
    try:
        parse_roles("tank:two")
        raise AssertionError("expected ValueError for non-int")
    except ValueError:
        pass
    try:
        parse_roles("tank:-1")
        raise AssertionError("expected ValueError for negative capacity")
    except ValueError:
        pass
    # parse_time_local returns UTC-aware dt
    dt = parse_time_local("20:30 30.09.2025")
    assert dt.tzinfo == timezone.utc
    # Raid without start time keeps None
    raid = Raid(
        id=1,
        guild_id=1,
        channel_id=1,
        message_id=None,
        name="Test",
        starts_at=0,
        comment="",
        max_participants=1,
        created_by=1,
        created_at=1,
    )
    assert raid.starts_dt is None

    # Integration scenarios with temporary DB
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp.close()
    global DB_PATH
    old_db_path = DB_PATH
    DB_PATH = tmp.name
    try:
        init_db()

        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        with with_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO raids (guild_id, channel_id, name, starts_at, comment, max_participants, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 1, "Test raid", 0, "", 2, 10, now_ts),
            )
            raid_id = cur.lastrowid
            cur.execute(
                "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                (raid_id, "tank", 2),
            )
            cur.execute(
                "INSERT INTO raid_roles (raid_id, role_name, capacity) VALUES (?, ?, ?)",
                (raid_id, "healer", 2),
            )
            # Initial signups: user 100 tank, user 200 healer
            cur.execute(
                "INSERT INTO raid_signups (raid_id, user_id, role_name, created_at) VALUES (?, ?, ?, ?)",
                (raid_id, 100, "tank", now_ts),
            )
            cur.execute(
                "INSERT INTO raid_signups (raid_id, user_id, role_name, created_at) VALUES (?, ?, ?, ?)",
                (raid_id, 200, "healer", now_ts + 1),
            )
            conn.commit()

        class DummyResponse:
            def __init__(self):
                self.messages: List[Optional[str]] = []

            async def send_message(self, content=None, *, ephemeral=False, embed=None, view=None):
                self.messages.append(content)

        class DummyUser:
            def __init__(self, user_id: int):
                self.id = user_id
                self.guild_permissions = type("Perms", (), {})()

        class DummyInteraction:
            def __init__(self, user_id: int):
                self.user = DummyUser(user_id)
                self.client = object()
                self.response = DummyResponse()

        async def run_async_tests() -> None:
            # Switching role in a full raid should succeed if limits stay valid
            interaction = DummyInteraction(100)
            await handle_signup(interaction, raid_id, "healer")
            assert any("Вы записались" in (msg or "") for msg in interaction.response.messages)
            with with_conn() as conn:
                row = conn.execute(
                    "SELECT role_name FROM raid_signups WHERE raid_id = ? AND user_id = ?",
                    (raid_id, 100),
                ).fetchone()
            assert row["role_name"] == "healer"

            # Reducing max participants should drop the latest signup
            edit_interaction = DummyInteraction(10)
            await raid_edit.callback(edit_interaction, raid_id, max_participants=1)  # type: ignore[attr-defined]
            assert any("Сняты" in (msg or "") for msg in edit_interaction.response.messages)
            signups_after = get_signups(raid_id)
            assert len(signups_after) == 1
            assert signups_after[0]["user_id"] == 100

        asyncio.run(run_async_tests())
    finally:
        DB_PATH = old_db_path
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ===================== Entrypoint =====================

def main():
    if os.getenv("RAIDBOT_SELFTEST") == "1":
        _selftest()
        print("Selftests: OK")
        return
    if not TOKEN:
        raise SystemExit(
            "Не найден токен: укажите DISCORD_TOKEN в .env/окружении или положите его в token.txt",
        )
    bot.run(TOKEN)


if __name__ == "__main__":
    main()

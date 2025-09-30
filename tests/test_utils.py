from __future__ import annotations

from datetime import datetime, timezone

import pytest

import config
import db
import utils
from models import Raid


def test_parse_roles_success() -> None:
    assert utils.parse_roles("tank:2, healer:3, dps:10") == {
        "tank": 2,
        "healer": 3,
        "dps": 10,
    }


def test_parse_roles_errors() -> None:
    with pytest.raises(ValueError):
        utils.parse_roles("badchunk")
    with pytest.raises(ValueError):
        utils.parse_roles("tank:two")
    with pytest.raises(ValueError):
        utils.parse_roles("tank:-1")


def test_parse_time_local_returns_utc() -> None:
    dt = utils.parse_time_local("20:30 30.09.2025")
    assert dt.tzinfo == timezone.utc


def test_raid_starts_dt_property() -> None:
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


def test_signup_flow_and_limits(monkeypatch, tmp_path) -> None:
    tmp_db = tmp_path / "raids.db"
    old_path = config.DB_PATH
    config.DB_PATH = str(tmp_db)
    try:
        db.init_db()
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        raid_id = db.create_raid(
            guild_id=1,
            channel_id=1,
            name="Test raid",
            starts_at=0,
            comment="",
            max_participants=2,
            created_by=10,
            roles={"tank": 2, "healer": 2},
        )
        db.add_signup(raid_id, 100, "tank", now_ts)
        db.add_signup(raid_id, 200, "healer", now_ts + 1)

        async def dummy_refresh(client, raid):
            return None

        pytest.importorskip("discord")

        import views

        monkeypatch.setattr(views, "refresh_message", dummy_refresh)

        class DummyResponse:
            def __init__(self) -> None:
                self.messages: list[str | None] = []

            async def send_message(self, content=None, **kwargs):
                self.messages.append(content)

        class DummyPerms:
            manage_events = False
            manage_guild = False

        class DummyUser:
            def __init__(self, user_id: int) -> None:
                self.id = user_id
                self.guild_permissions = DummyPerms()

        class DummyInteraction:
            def __init__(self, user_id: int) -> None:
                self.user = DummyUser(user_id)
                self.response = DummyResponse()
                self.client = object()

        async def run_flow() -> None:
            interaction = DummyInteraction(100)
            await views.handle_signup(interaction, raid_id, "healer")
            assert any("Вы записались" in (msg or "") for msg in interaction.response.messages)
            signup = db.get_user_signup(raid_id, 100)
            assert signup and signup.role_name == "healer"

            edit_interaction = DummyInteraction(10)

            from commands import raid_edit

            await raid_edit.callback(edit_interaction, raid_id, max_participants=1)
            assert any("Сняты" in (msg or "") for msg in edit_interaction.response.messages)
            signups_after = db.get_signups(raid_id)
            assert len(signups_after) == 1
            assert signups_after[0].user_id == 100

        import asyncio

        asyncio.run(run_flow())
    finally:
        config.DB_PATH = old_path

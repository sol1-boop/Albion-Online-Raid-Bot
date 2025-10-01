from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import db
import scheduler


def test_create_raid_and_reminders() -> None:
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    starts_at = now_ts + 3600
    raid_id = db.create_raid(
        guild_id=1,
        channel_id=1,
        name="Evening raid",
        starts_at=starts_at,
        comment="",
        max_participants=10,
        created_by=1,
        roles={"tank": 2, "healer": 2, "dps": 6},
        reminder_offsets=(1800, 600),
    )

    assert db.get_roles(raid_id) == {"dps": 6, "healer": 2, "tank": 2}
    assert db.get_raid_reminder_offsets(raid_id) == (1800, 600)

    db.reset_raid_reminders(raid_id, starts_at)
    reminders = db.list_reminders_for_raid(raid_id)
    assert len(reminders) == 2
    assert {rem.offset for rem in reminders} == {1800, 600}
    assert all(not rem.sent for rem in reminders)


def test_enforce_limits_and_promotion() -> None:
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    raid_id = db.create_raid(
        guild_id=1,
        channel_id=5,
        name="Tight roster",
        starts_at=0,
        comment="",
        max_participants=2,
        created_by=1,
        roles={"tank": 1, "healer": 1},
    )
    db.add_signup(raid_id, 100, "tank", now_ts)
    db.add_signup(raid_id, 200, "tank", now_ts + 1)
    db.add_signup(raid_id, 300, "healer", now_ts + 2)

    result = db.enforce_signup_limits(raid_id)
    assert result["waitlisted"] == [(200, "tank")]
    assert db.get_waitlist(raid_id)[0].user_id == 200

    db.remove_signup(raid_id, 100)
    promoted = db.promote_waitlist(raid_id)
    assert promoted == [(200, "tank")]
    signups = db.get_signups(raid_id)
    assert sorted(user.user_id for user in signups) == [200, 300]


def test_schedule_generation_creates_raid(monkeypatch, stub_client, stub_channel) -> None:
    async def run_flow() -> None:
        template_id = db.save_template(
            guild_id=1,
            name="Avalon",
            max_participants=5,
            roles={"tank": 2, "healer": 2, "dps": 6},
            comment="",
            reminder_offsets=(3600,),
        )
        template = db.fetch_template(1, "Avalon")
        assert template is not None

        next_run_at = int((datetime.now(tz=timezone.utc) + timedelta(minutes=30)).timestamp())
        local_now = datetime.now().strftime("%H:%M")
        schedule_id = db.create_schedule(
            guild_id=1,
            channel_id=99,
            template_id=template_id,
            name_pattern="Avalon %d.%m",
            comment="",
            max_participants=template.max_participants,
            roles_json=template.roles_json,
            weekday=datetime.now().weekday(),
            time_of_day=local_now,
            interval_days=7,
            lead_time_hours=1,
            reminder_offsets=None,
            next_run_at=next_run_at,
            created_by=777,
        )
        schedule_row = db.fetch_schedule(schedule_id)
        assert schedule_row is not None

        channel_type = type(stub_channel)
        monkeypatch.setattr(scheduler, "make_embed", lambda *args, **kwargs: {"raid": args[0].id})
        monkeypatch.setattr(scheduler, "SignupView", lambda raid_id: f"view:{raid_id}")
        monkeypatch.setattr(scheduler.discord, "TextChannel", channel_type)
        monkeypatch.setattr(scheduler.discord, "Thread", channel_type)

        created = await scheduler.maybe_generate_schedule_event(stub_client, schedule_row)
        assert created is True

        raid_ids = db.list_raid_ids()
        assert raid_ids
        raid = db.fetch_raid(max(raid_ids))
        assert raid is not None
        assert "Avalon" in raid.name
        assert stub_client.views == [f"view:{raid.id}"]
        assert stub_channel.sent_payloads

        updated_schedule = db.fetch_schedule(schedule_id)
        assert updated_schedule is not None
        assert updated_schedule.next_run_at > schedule_row.next_run_at

    asyncio.run(run_flow())


def test_reminder_service_sends_due_messages(monkeypatch, stub_client, stub_channel) -> None:
    async def run_flow() -> None:
        channel_type = type(stub_channel)
        monkeypatch.setattr(scheduler.discord, "TextChannel", channel_type)
        monkeypatch.setattr(scheduler.discord, "Thread", channel_type)

        raid_id = db.create_raid(
            guild_id=1,
            channel_id=99,
            name="Reminder test",
            starts_at=0,
            comment="",
            max_participants=5,
            created_by=1,
            roles={"tank": 2},
            reminder_offsets=(60,),
        )
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        db.reset_raid_reminders(raid_id, now_ts + 60, offsets=(60,))

        async def noop_process(self, now: int) -> None:
            return None

        monkeypatch.setattr(scheduler.ReminderService, "_process_schedules", noop_process, raising=False)

        service = scheduler.ReminderService(stub_client, interval_seconds=0)
        await service._tick()

        assert stub_channel.sent_payloads
        reminders = db.list_reminders_for_raid(raid_id)
        assert reminders[0].sent is True

    asyncio.run(run_flow())


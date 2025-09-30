# Raid waitlist, reminders and templates

## Summary

- Added a persistent waitlist/reserve queue with auto-promotion and channel notifications when slots become available.
- Implemented background scheduling for raid start reminders (60/15/5 minutes before the event).
- Introduced raid cloning plus template management commands to reuse role setups quickly.
- Made signup controls resilient to bot restarts by using deterministic component IDs.

## Notes

- Reminder schedules are stored in SQLite (`raid_reminders`) and processed by `ReminderService`.
- Waitlist entries are shown in the raid embed and managed via new database helpers.
- New slash commands: `/raid clone`, `/raid template create/list/delete/use`.

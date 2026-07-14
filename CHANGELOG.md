# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
- Fix: bedtime/school-time schedule matching (`bedtime_active`,
  `bedtime_enabled_today`, `schooltime_active`, `schooltime_enabled_today`,
  and the displayed bedtime/school-time start/end clock times) is now
  computed using the family's configured `TIMEZONE` instead of hardcoded
  UTC. Google's Family Link schedules are in local time, so evaluating
  "today"/"active right now" against UTC could compute the wrong weekday
  during the family's evening hours (once UTC's calendar date has already
  rolled over ahead of local time) and displayed clock times were off by
  the UTC offset from what's actually configured. Set `TIMEZONE` in `.env`
  (shared with the `familylink-auth` container) to your family's IANA
  timezone, e.g. `America/New_York`; defaults to UTC if unset.
- Fix: `apps_and_usage.appUsageSessions` (a rolling, unstably-ordered
  per-app usage-time window) and `apps_and_usage.apiHeader.serverTimestampMillis`
  (the API response's own timestamp) are now excluded from diffing. In a
  real production deployment these two fields alone accounted for ~92% of
  all recorded "changes" -- comparing usage sessions at shifting array
  indices produced constant false positives, and the timestamp field
  differs on literally every poll. Also drowned out real permission
  changes on the History page entirely.
- The History page and ntfy messages now show human-readable labels (e.g.
  "Chromebook: bedtime starts" instead of
  "applied_time_limits.devices.aannnppa....bedtime_window.start_ms") for
  known `applied_time_limits.*` fields, with device IDs resolved to
  friendly device names. Millisecond timestamps render as real
  dates/times, booleans as Yes/No, and null as "—". Less-understood raw
  paths (mainly `apps_and_usage.apps[N].*`) get a generic Title-Case
  fallback instead of the raw dotted path -- still shown in small print
  under the label for anyone who wants the raw value.
- Added a global "Notifications enabled" toggle on the Settings page to
  mute ntfy push alerts (changes still recorded in History) without
  touching poll interval or ntfy server/topic config.
- Added a "Poll now" button on the Status page to trigger an immediate poll
  cycle instead of waiting for the next scheduled interval.
- Fix: the first poll for a newly-added child no longer floods ntfy/history
  with hundreds of "changed from None" events (one per field). Google's
  Family Link API only exposes current state, not historical data, so the
  first poll now silently establishes a baseline instead. Subsequent polls
  diff normally and are correctly timestamped.
- Fix: raw, unparsed/noisy fields (rotating device thumbnail URLs, activity
  heartbeats, static capability flags, and the unparsed `time_limit`
  schedule-rules response) are now excluded from diffing by default, since
  they aren't meaningful permission changes and previously showed up as
  cryptic paths like `time_limit[1][0][1][0][0]`.
- Added a "Reset baseline" action on the Settings page to intentionally
  re-baseline a child (silently) after a batch of manual changes.
- Initial project scaffold: repo layout, secrets strategy, third-party
  attribution for `noiwid/HAFamilyLink` (MIT).
- Family Link auth/API client (ported, read-only), SQLModel data model +
  Alembic migrations, generic snapshot diff engine, ntfy alerting.
- Background poller/scheduler wiring auth, API client, diff engine, and ntfy
  together on a jittered interval.
- FastAPI web UI: first-run setup wizard, settings page, change history page.
- Dockerfile, docker-compose.yml (app + upstream familylink-auth), CI
  workflow publishing our image to GHCR, Dependabot config.

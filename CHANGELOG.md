# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
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

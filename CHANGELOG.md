# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
- Initial project scaffold: repo layout, secrets strategy, third-party
  attribution for `noiwid/HAFamilyLink` (MIT).
- Family Link auth/API client (ported, read-only), SQLModel data model +
  Alembic migrations, generic snapshot diff engine, ntfy alerting.
- Background poller/scheduler wiring auth, API client, diff engine, and ntfy
  together on a jittered interval.
- FastAPI web UI: first-run setup wizard, settings page, change history page.
- Dockerfile, docker-compose.yml (app + upstream familylink-auth), CI
  workflow publishing our image to GHCR, Dependabot config.

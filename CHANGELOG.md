# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
- Fixed a theme-persistence bug: the saved Light/Dark/Auto theme (set on the
  Settings page) previously only actually applied on the Settings page
  itself -- every other page (Status, History, first-run setup) silently
  ignored the saved value and always fell back to Auto, and the nav bar's
  quick theme-toggle button never persisted at all (it reset on the next
  page navigation). Both are now fixed: every page consistently reflects
  the saved theme, and the nav toggle button now saves immediately
  (cycling Auto -> Light -> Dark -> Auto) instead of being a client-only,
  per-tab flip.
- Added a persistent site logo/title (shield icon + "Family Link Alerts"
  wordmark) above the nav bar on every page.
- History page: added a "Filter by child" dropdown so you can narrow the
  change list down to a single child instead of always seeing everyone's
  changes mixed together.
- Settings page: moved "App Rules -- Always-blocked apps" to sit between
  "Children" and "Notifications" (previously it was last, after the
  Notifications/Polling/Display save button).
- **Visual redesign**: new warm amber/coral accent theme with light/dark
  mode support (auto-detects your browser/OS `prefers-color-scheme`, with
  a manual Auto/Light/Dark override saved in Settings -> Display). Added
  self-hosted Space Grotesk headings + a self-hosted Lucide icon set (no
  third-party CDN calls -- nothing about your browsing leaks to an
  external asset host). The old small text-link nav in the corner is now
  a single responsive segmented top nav bar (icon + label, same layout on
  phone and desktop). Settings is now organized into collapsible
  accordion sections (Connection, Children, Notifications, Polling,
  Display, App Rules) instead of one long scroll. The timezone field is
  now a real dropdown with a curated list of common zones (grouped by
  region) instead of a free-text box, with an "Other..." option for
  anything not listed. Status page gained a row of glanceable stat cards
  (auth health, session, children monitored, last poll); History rows now
  show a category icon.
- Settings page: added a "Timezone (for display only)" field so you can pick
  which IANA timezone the app uses to format timestamps and evaluate
  bedtime-schedule "active now" status across Status/History/ntfy messages.
  This is purely a display preference for this app -- it never reads from or
  writes to the real Google Family Link account's own timezone/settings.
  Defaults to the `TIMEZONE` value from `.env` until explicitly changed here;
  invalid entries are rejected with an explanatory message and the
  previously saved value is left untouched.
- Mobile-friendly layout: pages now stack table rows into readable
  label/value blocks below ~640px (phones in portrait) instead of
  squeezing multiple columns into an unreadably narrow view, nav links
  wrap instead of overflowing, and buttons/links go full-width for easier
  tapping.
- Settings page: added a "Notify me about" section with a checkbox per
  change category (app blocking, screen time & limits, bedtime/school
  time, device lock, polling issues, everything else) to control which
  categories of detected change actually push a ntfy alert. Unchecking a
  category only mutes its push notification -- the change is still
  recorded and visible on the History page either way. Defaults to every
  category enabled (matching prior behavior) until explicitly changed.
- Settings page now shows the running app version (and a link to this
  changelog) in the footer, so it's easy to confirm what's actually
  deployed.
- Status and History pages now show the time of the last successful poll
  (per-child on History, most-recent-across-all-children on Status), so
  it's easy to spot a stalled poller without digging into logs.
- Child avatars and per-app icons (both sourced from Family Link's own
  data -- `profile.profileImageUrl` for family members, `iconUrl` for each
  app) are now displayed next to child names and app titles across the
  Settings and History pages. A lightweight per-poll refresh keeps a
  child's avatar in sync with their current Google profile photo,
  including self-healing it for children set up before this field
  existed.
- Status page: added a new "Screen time today" section showing, per
  child, total minutes used across all their devices today plus a
  per-device breakdown (used/remaining/daily limit/bonus time, and
  bedtime/school-time/locked badges). Devices with no usage yet today are
  collapsed by default; devices already in use today are expanded.
- Fix: re-enabling an always-blocked app and having it auto re-blocked
  within the same poll cycle used to produce **no history record at all**
  and only an ephemeral ntfy push for the reblock -- enforcement patched
  the just-fetched snapshot's `hidden` flag back to `True` *before*
  diffing against the stored snapshot, so old and patched-new looked
  identical and no ChangeEvent was ever created. Diffing now always runs
  against the true fetched state first (so "someone re-enabled it" is
  recorded/notified normally), and the re-block itself is now also
  recorded as its own ChangeEvent with a dedicated notification, so both
  are visible on the History page and both actually reach ntfy.
- The app-blocked/unblocked field (`apps_and_usage.apps[N].supervisionSetting.hidden`)
  now resolves to the app's actual name in History labels and ntfy
  messages (e.g. "TikTok: blocked") instead of a bare positional array
  index (e.g. "Apps #7 → Hidden"). The raw index is rewritten to the app's
  stable package name at diff time, so labels stay correct even if
  Family Link's app list reorders between polls.
- History page: the field-path detail under each change is now hidden
  inside a collapsible `<details>` disclosure instead of always showing --
  expanding it also now shows the raw underlying old/new values (when they
  differ from the humanized display) and whether a ntfy alert was sent for
  that change.
- History page: the changes table now wraps long text within its columns
  instead of overflowing horizontally, and the page itself is slightly
  wider (960px vs. the default 780px) so the table has more room.
- Minute-duration fields (screen time used/remaining/allowed, daily limit,
  bonus time) are now rendered as "1h 15m" instead of a bare number of
  minutes.
- Fix: the "Blocked apps" auto-discovery also now excludes OEM/carrier
  system apps by known package-name prefix (`com.samsung.`, `com.sec.`,
  `com.tmobile.`, etc.), since some of them (e.g. Samsung's "Reminder",
  "Modes and Routines") have a real install timestamp despite being
  pre-installed bloat, so the `installTimeMillis == 0` check alone missed
  them. Verified against live data: cut one child's list from 34 down to
  the apps a parent actually recognizes.
- Fix: the "Blocked apps" auto-discovery (see below) now skips pre-installed
  OEM/carrier system apps (Samsung Knox internals, "Galaxy Finder", etc.),
  identified by `installTimeMillis == 0`. On real production data this cut
  one child's list from ~100 entries down to the handful actually worth a
  parent's attention.
- Fix: `get_app_package_name()` now checks the flat `packageName` field
  first (confirmed live -- `apps_and_usage.apps[N].packageName`), falling
  back to the `appId.androidAppPackageName` shape only if that's absent.
- Added an opt-in "always blocked" app enforcement feature: the Settings
  page now lists every app ever seen blocked in Family Link, per child,
  in an expandable "Blocked apps" section. Checking "Always blocked" for
  an app means that if a future poll finds it enabled again, the app
  immediately calls Family Link's block-app endpoint to re-block it in the
  same poll cycle, and sends a dedicated high-priority ntfy alert
  confirming the auto-re-block (in addition to the normal change alert for
  the "re-enabled" event itself). This is the project's first write/
  mutation capability -- see `third_party/NOTICE.md` for attribution and
  the README's "Always-blocked apps" section for details/caveats.
- Fix: `LatestSnapshot.updated_at` was never bumped on subsequent polls
  (only `data` was refreshed), so the stored timestamp looked stale even
  though the snapshot itself was current.
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

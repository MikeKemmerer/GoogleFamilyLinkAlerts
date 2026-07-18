# Changelog

All notable changes to this project are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
- **App usage colors are now assigned by usage rank, not hashed from the
  package name**, so two apps that happen to land next to each other in
  the usage-sorted summary bar or stacked hourly chart always get
  different, well-contrasted colors instead of occasionally repeating or
  clashing. The color palette itself was also reordered so consecutive
  entries have maximally distinct hues.
- **History page: renamed "location fixes" to "locations" in user-facing
  text** (e.g. "No recorded locations yet", "3 recorded locations").
- **History page: moved the Play/Pause button and scrub slider to sit
  below the map** (previously alongside it in a side panel), so the map
  gets the full width of the card.
- **History page: "Detected changes" is now collapsible**, and the number
  of events shown per page is configurable (25/50/100, saved via a
  dropdown in the filter bar).
- **History page: the location-history map is now embedded directly on
  the page** (in its own collapsible section) instead of living behind a
  separate "view location history" link/page swap. It has its own child
  selector, independent of the main event filter, plus Play/Pause controls
  that auto-advance the replay slider through recorded fixes.
- **Fixed a remaining timezone mismatch on the Status page**: the "Usage
  over the day" hourly chart was aggregated using "today" per the
  configured *display* timezone, while the per-app usage list underneath
  it (and the device's own usage total) is anchored to the device's own
  day. The hourly chart now uses that same device-anchored day, so it no
  longer disagrees with the per-app breakdown directly above it.
- **Reverted the "Usage over the day" chart back to smoothed/linear
  rendering** between hourly data points (per user feedback), while
  keeping the previously-fixed viewport clipping (never plots past the
  current hour) and correct stacked-sum y-axis scaling.
- Documented, via an in-app hint, the one usage-total discrepancy that
  can't be fixed in this app: Family Link's own "used today" total (used
  for daily-limit enforcement) uses its own internal day boundary, which
  is not guaranteed to match the device-anchored day used for the per-app
  breakdown/hourly chart.
- **Fixed the Status page map regression from the re-center control**
  (introduced right after it shipped): the accuracy marker/circle were
  being attached to the map *after* the first `setView()` call instead of
  before, which reproduced the exact "map never renders" bug from a few
  versions ago -- no pin, no accuracy circle, and no visible re-center/zoom
  controls (the crash happened before those lines ran). Layers are now
  attached before the map's first view is set, as Leaflet requires. Also
  re-enabled the on-map zoom control (+/-), which had been hidden.
- **Fixed the "Usage over the day" chart's y-axis scaling to the wrong
  value.** It was scaling to the single highest-usage app's own total
  instead of the *sum* of every app's usage at the tallest hour (the true
  top of the stacked area) -- so the stacked area could visually extend
  past the top of the chart whenever more than one app had usage that
  day.
- **Fixed a mismatch between "total time used today" and the sum of the
  per-app usage breakdown.** The per-app breakdown was aggregated using
  "today" per the configured *display* timezone, while Family Link's own
  device usage total uses its own (not necessarily matching) day
  boundary. The per-app breakdown now aggregates whichever calendar day is
  most recent in Family Link's own usage data, keeping it aligned with the
  device total regardless of the display timezone setting.
- **Added a y-axis to the "Usage over the day" chart** (Status page):
  cumulative-time tick labels (0/25/50/75/100% of the day's max) plus
  matching dashed gridlines, laid out so the top and bottom labels are
  never clipped.
- **Added a re-center control to the device-location maps** (Status and
  History pages) -- a small button (top-left, matching Leaflet's usual
  zoom-control styling) that resets the view if you've panned/zoomed away.
  On the History page it re-fits the whole route; on the Status page it
  re-centers on the device's last known point.
- **Fixed the "Usage over the day" chart plotting flat, misleading data
  into hours of the day that haven't happened yet**, and switched it from
  linear interpolation between sparse hourly points to a proper step
  chart (flat within each hour, vertical jump at hour boundaries) -- this
  matches the actual hourly resolution of the underlying data instead of
  implying gradual/continuous usage between polls. The x-axis now always
  ends at "now" instead of projecting all the way to 23:00.
- **Increased the device-location map height** (190px -> 320px on both the
  Status and History pages) -- it was rendering noticeably squashed for
  its width.
- **Fixed device-location maps on the Status page never rendering at all
  when accuracy data was present.** The accuracy-circle overlay's
  `getBounds()` was being called before the map had an initial view/zoom
  set, which throws in Leaflet (the circle never finishes attaching to the
  map) -- the whole map init silently failed as a result. The map now
  always gets an initial `setView()` before any bounds-dependent layer is
  added, so it renders correctly.
- **Fixed icon rendering reliability.** Icons are now inlined directly into
  every page instead of being referenced from a separate `/static/icons.svg`
  file via a cross-document `<use href="...">`. The external-file approach
  was unreliable on some mobile browsers (symbols could silently fail to
  render, especially once a browser had cached an older copy of the sprite
  from before a given icon was added) -- inlining removes that entirely.
  Also added the missing `stroke-width`/`stroke-linecap`/`stroke-linejoin`
  defaults so icon strokes render at their intended weight instead of a
  barely-visible 1px hairline.
- **Fixed the logout/account nav items disappearing on narrow (phone-width)
  screens.** The top nav bar didn't account for the extra items added by the
  optional auth feature (username/role badge, logout button) -- on narrow
  viewports the row could overflow its container (which clips instead of
  wrapping), pushing the logout button off-screen. The username/role text
  now hides (icon-only) below 640px instead of forcing an overflow.
- **Fixed device-location maps sometimes rendering blank/grey.** Map
  initialization now waits for the page to fully finish loading (instead of
  running as soon as the script tag is reached) and calls
  `invalidateSize()` shortly after creating the map, which is the standard
  fix for Leaflet maps created while their container's layout hadn't fully
  settled yet. Map init errors are now logged to the browser console
  instead of failing silently.
- **New: "Usage over the day" stacked-area chart** (Status page, under an
  expandable "Usage over the day" disclosure below the existing per-app
  usage summary bar). Shows cumulative per-app screen time across the hours
  of the day. Important caveat: Family Link's API only reports a running
  per-app *daily* total, with no per-session timestamps at all, so this
  can't be computed retroactively -- the poller now records the usage
  observed *since the previous poll* into the hour it was detected in, so
  data only starts accumulating from when you upgrade to this version, at a
  resolution limited by your poll interval.
- **Fixed: location tracking couldn't actually be turned on.** The
  `location_tracking_enabled` setting added in the previous release had no
  UI control anywhere -- Settings now has a "Location" section with a
  "Location tracking" checkbox (admin only) so the Status page's
  map/battery display can actually be enabled.
- Bonus/extra time notifications and History no longer show the raw,
  meaningless bonus-time override ID. Instead, when Family Link records
  which parent/guardian granted the bonus, this app now shows "granted by
  <name>" alongside the existing bonus-minutes entry. This relies on an
  unconfirmed field position in Google's undocumented API, so it's
  designed to fail silently (show nothing extra) rather than show
  incorrect data if that assumption turns out to be wrong for your account.
- Status page: added a stacked per-app usage chart (with legend) showing
  how a child's screen time today is split across individual apps, using
  data Family Link already reports per poll. This is purely a display
  addition -- no new History/notification entries are generated for
  per-app usage (that data was already excluded from change-tracking to
  avoid noise).
- **New optional feature: device location & battery.** Off by default --
  turn on "Location tracking" in Settings to start recording each child's
  last-known device location (latitude/longitude/accuracy/place name) and
  the reporting device's battery level, ported from HAFamilyLink's
  `async_get_location`. The Status page's "Screen time today" section is
  now "Device activity" and shows a small embedded map (self-hosted
  Leaflet, no third-party map/JS CDN) plus a battery badge per device with
  location data. Map tiles are proxied and cached through this app's own
  server (`GET /tiles/{z}/{x}/{y}.png`) so no third party ever sees your
  browser's IP alongside the map area you're viewing. History gets a new
  "Location" category/icon, plus a dedicated location-history view (select
  a child, scrub through past fixes on a map with a time slider) --
  reconstructed automatically from the same change-history mechanism every
  other tracked field already uses, no new storage needed. Guest view-only
  access has independent, admin-configurable toggles for both location and
  battery visibility (defaults to hidden, like every other guest
  category).
- **New optional feature: login & role-based access.** Off by default (no
  behavior change for existing installs) -- turn it on in
  Settings > Access & Users, which prompts you to create the first admin
  account before login actually takes effect (so you can't lock yourself
  out). Three roles: **Admin** (full access, including this new Access &
  Users section), **Contributor** (Children + App Rules management only --
  the same day-to-day toggles as before, nothing else), and **Viewer**
  (read-only Status + History). Also supports a "Continue as guest"
  option on the login page (only shown when an admin enables it), with a
  fully granular, admin-only-configurable permission panel controlling
  exactly which pages, which children, and which data categories (screen
  time, bonus time, app blocking, bedtime/school time) a guest may see --
  everything defaults to hidden when guest mode is first turned on.
  Passwords are hashed with bcrypt; sessions use a signed cookie (secret
  generated once, stored alongside the rest of the app's data).
  Logged-in users (any role) can also set their own personal display
  timezone and theme on a new `/account` page, overriding the site-wide
  defaults for their own account only.
- Bumped `bcrypt` to `5.0.0` and added `itsdangerous` (required by
  Starlette's session middleware, used by the new login system).
- Settings > Children: added an "Auto-revoke bonus time" checkbox per
  child. When enabled, the poller automatically cancels (revokes) any
  active granted bonus/extra screen time on that child's devices on the
  very next polling interval, so a one-off bonus grant doesn't silently
  become a standing increase to the daily limit. Uses a new
  `cancel_time_bonus` write against Family Link's API (the same
  DELETE-via-POST convention Google's own client uses), the project's
  second sanctioned write capability after "always-blocked apps"
  re-blocking.
- Notification settings: split the "Screen time & limits" category into
  two separate categories -- "Screen time & limits" (daily limit,
  used/remaining) and a new "Bonus/extra time" category (bonus granted,
  bonus revoked) -- so you can enable/disable notifications for each
  independently. **If you've already customized your notification
  categories**, you'll need to revisit Settings > Notifications and check
  the new "Bonus/extra time" box if you still want alerts about granted or
  auto-revoked bonus time; brand-new installs get both enabled by default.
- Added a favicon (self-hosted SVG, reuses the shield logo) so the browser
  tab/bookmark icon isn't the generic blank-page icon.
- History page: the Detected changes table is now paginated (50 per page,
  Newer/Older navigation) instead of hard-capping at the most recent 200
  events with no way to see older ones. The child filter is preserved
  across pages.
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

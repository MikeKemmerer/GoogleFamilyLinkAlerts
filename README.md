# GoogleFamilyLinkAlerts

Get notified the moment something changes in your kids' Google Family Link
settings — screen time limits, per-app time limits, bedtime/school time,
allowed/blocked apps, and (planned) website filters — instead of finding out
by accident.

## ⚠️ Important disclaimer

Google Family Link has no official, public API. This project works by
authenticating as a Google account you control and calling Google's internal
(reverse-engineered) Family Link endpoints. **This is unofficial, may violate
Google's Terms of Service, and could theoretically result in account
action.** Use a dedicated secondary-parent Google account rather than your
primary account, and use at your own risk. This project is not affiliated
with, endorsed by, or connected to Google LLC.

## How it works

- An unmodified copy of
  [`noiwid/HAFamilyLink`](https://github.com/noiwid/HAFamilyLink)'s
  standalone `familylink-auth` container runs a real Chromium browser
  (via Playwright) behind a noVNC web view, so you can complete the actual
  Google login (including 2FA) through your browser, once. It then serves
  the resulting session cookies over a small local API.
- This app polls Family Link's internal endpoints (adapting reverse-engineered
  logic from the same project, MIT licensed — see
  [`third_party/NOTICE.md`](third_party/NOTICE.md)) using those cookies,
  takes a snapshot of every child's settings, and diffs it against the last
  snapshot stored in SQLite.
- Any difference — screen time, app limits, bedtime, school time, location,
  device lock state, (planned) website filters — is recorded in a change
  history and pushed to you via [ntfy](https://ntfy.sh).
- A small web UI provides a first-run setup wizard, ongoing settings, auth
  status, and the change history/timeline.

## Status

This project is under active initial development. See `CHANGELOG.md` for
progress and `plan.md`-equivalent design notes below.

## Quick start (once published)

```bash
curl -O https://raw.githubusercontent.com/<owner>/GoogleFamilyLinkAlerts/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/<owner>/GoogleFamilyLinkAlerts/main/.env.example
cp .env.example .env   # edit the API key / VNC password
docker compose up -d
```

Then open the app's web UI to complete the first-run setup wizard (it will
guide you through the noVNC Google login and auto-discover your children).

## Local development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## License

MIT — see [`LICENSE`](LICENSE). Incorporates and runs code/images from
`noiwid/HAFamilyLink` (MIT) — see [`third_party/NOTICE.md`](third_party/NOTICE.md).

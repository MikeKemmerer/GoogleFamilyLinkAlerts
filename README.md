# GoogleFamilyLinkAlerts

Get notified the moment something changes in your kids' Google Family Link
settings — screen time limits, per-app time limits, bedtime/school time,
allowed/blocked apps, device lock state, and (planned) website filters —
instead of finding out by accident.

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
- Any difference — screen time, app limits, bedtime, school time, device lock
  state, (planned) website filters — is recorded in a change history and
  pushed to you via [ntfy](https://ntfy.sh).
- A small web UI provides a first-run setup wizard, ongoing settings, auth
  status, and the change history/timeline.

## Quick start

```bash
mkdir familylink-alerts && cd familylink-alerts
curl -O https://raw.githubusercontent.com/MikeKemmerer/GoogleFamilyLinkAlerts/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/MikeKemmerer/GoogleFamilyLinkAlerts/main/.env.example
cp .env.example .env
# Edit .env: set VNC_PASSWORD and FAMILYLINK_AUTH_API_KEY to your own values.

mkdir -p data/familylink-auth data/app
chown -R 1000:1000 data/app   # our app container runs as uid 1000, non-root

docker compose up -d
```

Then open `http://<this-host>:8080` and follow the first-run setup wizard
(details below). No config files need manual editing beyond `.env`.

### First-run setup wizard walkthrough

1. **Sign in to Google.** The wizard checks whether `familylink-auth` already
   has a valid session. If not, it links you to the noVNC login screen at
   `http://<this-host>:6080` — connect (password = your `VNC_PASSWORD`), sign
   in with your secondary/parent Google account, complete 2FA if prompted,
   then come back and click "I've logged in / Refresh".
2. **Choose children to monitor.** Once authenticated, the wizard
   auto-discovers every supervised child on that Google family and lets you
   toggle which ones to monitor. No manual entry needed for the common case.
3. **Alerts & polling.** Enter an [ntfy](https://ntfy.sh) server URL + topic
   (use a hard-to-guess topic name, or a self-hosted ntfy server, since
   anyone who knows a public ntfy.sh topic can read your alerts) and a poll
   interval in minutes. Finishing this step completes setup — you land on the
   ongoing Settings page, which lets you change any of this later.

After setup, the poller runs on a schedule (with jitter) in the background;
any detected change and any polling failure (e.g. an expired session) is
pushed to your ntfy topic, and everything is recorded on the History page.

## Re-authentication

Google sessions eventually expire. When that happens:

1. The next poll attempt fails; you get a ntfy alert and a `PollFailure` row
   appears on the History page.
2. Open the app's Settings (or Status) page — it shows the session as
   "not logged in" with a direct link to the `familylink-auth` noVNC login.
3. Complete the Google login again as in step 1 of the setup wizard above.
   No restart or reconfiguration needed; the next poll cycle picks up the
   refreshed session automatically.

## Updating

Two independently-versioned images run in this stack — update them
deliberately, not automatically:

- **Our `app` image**: `.github/dependabot.yml` and CI publish semver tags
  (`vX.Y.Z`) plus a moving `edge` tag (latest commit on `main`). Check
  [`CHANGELOG.md`](CHANGELOG.md) for what changed, set `APP_IMAGE_TAG` in
  `.env` to the version you want (or leave as `latest`), then:

  ```bash
  docker compose pull app
  docker compose up -d app
  ```

  Any pending database schema migrations run automatically on startup — no
  manual migration step required.

- **Upstream `familylink-auth`**: pinned directly in `docker-compose.yml`
  (not a floating tag) for reproducibility. Dependabot watches this line and
  opens a PR when [noiwid/HAFamilyLink](https://github.com/noiwid/HAFamilyLink)
  cuts a new release — review its
  [changelog](https://github.com/noiwid/HAFamilyLink/blob/main/familylink-playwright/CHANGELOG.md)
  (it handles the sensitive login/cookie flow) before merging, then:

  ```bash
  docker compose pull familylink-auth
  docker compose up -d familylink-auth
  ```

### Rollback

If an update misbehaves, re-pin the previous tag (in `.env` for `app`, or
directly in `docker-compose.yml` for `familylink-auth`) and re-run
`docker compose up -d`. Both containers' state lives entirely in the
`data/` bind mounts, so rolling back the image doesn't lose history —
though a schema migration from a newer `app` version is not automatically
reversed, so prefer rolling back promptly if you hit a bad release.

## Configuration reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `FAMILYLINK_AUTH_API_KEY` | — | Shared secret protecting `familylink-auth`'s `/api/cookies` endpoint. Required in practice — that endpoint returns your full Google session. |
| `FAMILYLINK_AUTH_BASE_URL` | `http://familylink-auth:8099` | Container-to-container URL our app uses; leave as-is with the provided compose file. |
| `FAMILYLINK_AUTH_NOVNC_URL` | `http://localhost:6080` | Browser-facing noVNC URL, shown as a login link in the web UI. Set to your Docker host's IP/hostname if accessing remotely. |
| `VNC_PASSWORD` | — | Password for the noVNC session used to complete Google login. |
| `APP_IMAGE_TAG` | `latest` | Our app's image tag — pin to a specific `vX.Y.Z` release for reproducibility. |
| `APP_DATA_DIR` | `/data` | In-container path where the SQLite DB lives (bind-mounted to `./data/app`). |
| `APP_PORT` / `APP_HOST_PORT` | `8080` | In-container / published host port for the web UI. |

Everything else (children to monitor, ntfy target, poll interval) is
configured through the web UI, not `.env` — see the setup wizard above.

## Local development

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest -q
```

Run the app itself (against a local SQLite file, without Docker):

```powershell
$env:APP_DATA_DIR = "$PWD\data"
$env:FAMILYLINK_AUTH_BASE_URL = "http://localhost:8099"
uvicorn app.main:app --reload --port 8080
```

You'll still need a running `familylink-auth` container (see
`docker-compose.yml`) for anything beyond the "not logged in" wizard stage.

## Project layout

See the module docstrings under `app/` for details — in short:
`app/familylink/` talks to Google and the auth container, `app/diff/`
compares snapshots, `app/notify/` sends ntfy alerts, `app/poller.py` ties
them together on a schedule, `app/web/` is the FastAPI UI, and `app/db/` is
the SQLModel schema + Alembic migrations.

## License

MIT — see [`LICENSE`](LICENSE). Incorporates and runs code/images from
`noiwid/HAFamilyLink` (MIT) — see [`third_party/NOTICE.md`](third_party/NOTICE.md).

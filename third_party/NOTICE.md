# Third-party attribution

This project reuses work from other open-source projects. Per the terms of
their licenses, the original copyright notices and license text are
reproduced here and/or alongside the adapted code.

## noiwid/HAFamilyLink

- **Source:** https://github.com/noiwid/HAFamilyLink
- **License:** MIT (see [`HAFamilyLink_LICENSE`](./HAFamilyLink_LICENSE) in
  this directory for the full, unmodified license text)
- **Copyright:** (c) 2025 Vortitron 2000

**What we use from it, and how:**

1. **`familylink-auth` Docker image** — we run the upstream
   `ghcr.io/noiwid/familylink-auth` image **unmodified** as a separate
   container in `docker-compose.yml`. We do not vendor or modify its source;
   we only consume its `/api/cookies` HTTP endpoint over the network. This
   image handles the interactive Google login (via a Playwright/Chromium
   browser exposed through noVNC) and produces authenticated session cookies.

2. **Reverse-engineered Family Link API calls** — `app/familylink/api_client.py`
   adapts logic originally written for
   `custom_components/familylink/client/api.py` in HAFamilyLink (SAPISID
   cookie-based requests to Google's internal Family Link endpoints for
   screen time, per-app limits/blocking, bedtime, school time, GPS location,
   device lock/unlock, and family/child info). Functions adapted from that
   source carry a comment noting their origin. This project's own additions
   (website-filter monitoring, generic snapshot diffing, alerting, and the
   web UI) are original work.

Both pieces are used under the MIT license's permissive terms, which allow
use, modification, and redistribution provided the copyright notice and
license text are retained — hence this file and the accompanying license
copy.

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

3. **App block/unblock mutation calls** — `FamilyLinkApiClient.block_app`/
   `unblock_app` (`app/familylink/api_client.py`) are adapted from
   HAFamilyLink's `async_block_app`/`async_unblock_app`
   (`apps:updateRestrictions` endpoint). This is the one write/mutation
   capability this project supports, used narrowly by the opt-in "always
   blocked" app-enforcement feature (see the Settings page and
   `app/poller.py:_enforce_always_blocked_apps`) — everything else remains
   read-only monitoring.

4. **Location lookup/parsing** — `FamilyLinkApiClient.get_location`
   (`app/familylink/api_client.py`) is adapted from HAFamilyLink's
   `async_get_location` (`families/mine/location/{childId}` endpoint,
   including the protobuf-like positional-array response parsing and
   `locationRefreshMode` request params).

Both pieces are used under the MIT license's permissive terms, which allow
use, modification, and redistribution provided the copyright notice and
license text are retained — hence this file and the accompanying license
copy.

## Leaflet

- **Source:** https://leafletjs.com/ and https://unpkg.com/leaflet@1.9.4/dist/
- **License:** BSD-2-Clause
- **Copyright:** (c) 2010-2023 Vladimir Agafonkin; (c) 2010-2011 CloudMade

**What we use from it, and how:**

We vendor Leaflet's unmodified browser assets in `app/static/leaflet/`
(`leaflet.js`, `leaflet.css`, and the default marker images) to render the
Status page's embedded last-known device-location maps without loading any
JavaScript, CSS, or images from a third-party CDN at runtime.

BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

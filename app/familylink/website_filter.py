"""Allowed/blocked website (URL content filter) monitoring.

STATUS: NOT YET IMPLEMENTED -- open research item.

Unlike screen time, app limits, bedtime, and school time, no one (including
noiwid/HAFamilyLink, our reference implementation for the rest of the API)
has published a reverse-engineered endpoint for Family Link's website
allow/block list. This needs a live research pass against a real,
authenticated Family Link session, which the agent that scaffolded this
project cannot perform (no access to a real Google account).

## How to do the research pass

1. Get the `familylink-auth` container running and complete the Google login
   via its noVNC page (see README "First-run setup").
2. Open the noVNC browser tab (or, once cookies are captured, load
   `https://families.google.com/families/...` -- exact URL TBD -- in your
   own browser with devtools open) and navigate to the child's
   "Content restrictions" / "Manage sites" page.
3. Open the Network tab, filter for XHR/fetch requests, and reproduce:
   - Toggling "Try to block explicit sites" on/off
   - Adding/removing an entry from the always-allowed or always-blocked list
   - Simply loading the page (to capture the *read* request, which is what
     this module needs)
4. Note the request URL, method, headers (especially whether it reuses the
   same SAPISIDHASH scheme as `api_client.py`), and the exact response
   shape (JSON object vs. positional array like `appliedTimeLimits`).
5. Share the sanitized request/response (redact the child's real name/email
   and any account IDs you don't want public) so `get_website_filter` below
   can be implemented against real data.

## Fallback if no API exists

If Google truly does not expose this over the same internal API surface,
fall back to scraping the single "Manage sites" page: reuse the captured
session cookies to drive a lightweight authenticated HTTP GET (or, if the
data is only rendered client-side, a headless-browser page load restricted
to that one page) and parse the rendered list. This avoids full-dashboard
scraping while still covering the gap.
"""
from __future__ import annotations

from typing import Any

from .api_client import FamilyLinkApiClient


class WebsiteFilterNotImplementedError(NotImplementedError):
    """Raised until this module is implemented against real captured data."""


async def get_website_filter(client: FamilyLinkApiClient, account_id: str) -> dict[str, Any]:
    """Return the allowed/blocked website filter state for a child.

    Currently unimplemented -- see module docstring. The poller treats this
    as an optional data source and will skip it (logging a one-time notice)
    rather than fail the whole poll cycle until this is implemented.
    """
    raise WebsiteFilterNotImplementedError(
        "Website filter monitoring is not yet implemented -- see "
        "app/familylink/website_filter.py for the research steps needed."
    )

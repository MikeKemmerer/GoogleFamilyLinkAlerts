"""Exception types for the Family Link client.

Mirrors the exception hierarchy used by noiwid/HAFamilyLink so error-handling
logic ported from that project (see api_client.py) doesn't need to change.
"""
from __future__ import annotations


class FamilyLinkError(Exception):
    """Base exception for all Family Link client errors."""


class AuthenticationError(FamilyLinkError):
    """Raised when authentication is missing, invalid, or rejected."""


class SessionExpiredError(FamilyLinkError):
    """Raised when Google rejects a request with 401 (session/cookies stale)."""


class NetworkError(FamilyLinkError):
    """Raised when a request to Google's API fails for network/HTTP reasons."""

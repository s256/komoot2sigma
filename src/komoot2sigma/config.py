"""Credential and configuration management.

Credentials are stored in `credentials.json` in the current working
directory, matching KomootGPX's behavior.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

CREDENTIALS_FILE = Path("credentials.json")


def load_credentials() -> dict[str, Any]:
    """Load stored credentials from CWD/credentials.json."""
    if CREDENTIALS_FILE.exists():
        with open(CREDENTIALS_FILE) as f:
            return json.load(f)
    return {}


def save_credentials(credentials: dict[str, Any]) -> None:
    """Persist credentials to CWD/credentials.json."""
    with open(CREDENTIALS_FILE, "w") as f:
        json.dump(credentials, f, indent=2)
    CREDENTIALS_FILE.chmod(0o600)


def get_komoot_credentials() -> dict[str, str] | None:
    """Return komoot credentials or None if not configured."""
    creds = load_credentials()
    komoot = creds.get("komoot")
    if not komoot:
        return None
    if not komoot.get("user_id") or not komoot.get("token"):
        return None
    return komoot


def save_komoot_credentials(
    user_id: str, token: str, display_name: str
) -> None:
    """Store komoot session credentials."""
    creds = load_credentials()
    creds["komoot"] = {
        "user_id": user_id,
        "token": token,
        "display_name": display_name,
        "date": time.time(),
    }
    save_credentials(creds)


def get_sigma_credentials() -> dict[str, str] | None:
    """Return sigma credentials or None if not configured."""
    creds = load_credentials()
    sigma = creds.get("sigma")
    if not sigma:
        return None
    if not sigma.get("access_token"):
        return None
    return sigma


def save_sigma_credentials(
    access_token: str,
    refresh_token: str | None = None,
    expires_in: int | None = None,
) -> None:
    """Store sigma OAuth credentials."""
    creds = load_credentials()
    sigma_creds: dict[str, Any] = {
        "access_token": access_token,
        "date": time.time(),
    }
    if refresh_token:
        sigma_creds["refresh_token"] = refresh_token
    if expires_in:
        sigma_creds["expires_at"] = time.time() + expires_in
    creds["sigma"] = sigma_creds
    save_credentials(creds)


def get_synced_tours() -> set[str]:
    """Return set of Komoot tour IDs that have been uploaded to Sigma."""
    creds = load_credentials()
    return set(creds.get("synced_tours", []))


def mark_tour_synced(tour_id: str) -> None:
    """Record that a tour has been uploaded to Sigma."""
    creds = load_credentials()
    synced = set(creds.get("synced_tours", []))
    synced.add(tour_id)
    creds["synced_tours"] = sorted(synced)
    save_credentials(creds)

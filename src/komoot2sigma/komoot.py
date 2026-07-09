"""Komoot API client - thin wrapper around komootgpx."""

from __future__ import annotations

from dataclasses import dataclass

from komootgpx.api import KomootApi
from komootgpx.gpxcompiler import GpxCompiler


@dataclass
class KomootTour:
    """Represents a Komoot tour/route."""

    tour_id: str
    name: str
    sport: str
    distance: float
    duration: int
    elevation_up: float
    elevation_down: float
    tour_type: str

    @property
    def distance_km(self) -> float:
        return self.distance / 1000.0

    def summary(self) -> str:
        return (
            f"[{self.tour_id}] {self.name} "
            f"({self.distance_km:.1f} km, {self.sport}, {self.tour_type})"
        )


class KomootClient:
    """Client for the Komoot API, backed by komootgpx."""

    def __init__(self):
        self._api = KomootApi()

    @classmethod
    def from_credentials(cls, user_id: str, token: str) -> KomootClient:
        """Create a client from existing session credentials."""
        client = cls()
        client._api.user_id = user_id
        client._api.token = token
        return client

    def login(self, email: str, password: str) -> tuple[str, str, str]:
        """Authenticate with Komoot and return (user_id, token, display_name)."""
        user_id, token, display_name = self._api.login(email, password)
        return user_id, token, display_name

    def list_tours(self, tour_type: str = "tour_all") -> list[KomootTour]:
        """List tours for the authenticated user.

        Args:
            tour_type: Filter by type ("tour_planned", "tour_recorded", "tour_all").
        """
        raw_tours = self._api.fetch_tours(tour_type=tour_type, silent=True)
        tours: list[KomootTour] = []
        for tour_id, tour_data in raw_tours.items():
            tours.append(KomootTour(
                tour_id=str(tour_id),
                name=tour_data.get("name", "Unnamed"),
                sport=tour_data.get("sport", "unknown"),
                distance=tour_data.get("distance", 0),
                duration=tour_data.get("duration", 0),
                elevation_up=tour_data.get("elevation_up", 0),
                elevation_down=tour_data.get("elevation_down", 0),
                tour_type=tour_data.get("type", "unknown"),
            ))
        return tours

    def list_planned_tours(self) -> list[KomootTour]:
        """List only planned (not yet recorded) tours."""
        return self.list_tours(tour_type="tour_planned")

    def download_gpx(self, tour_id: str) -> bytes:
        """Download tour as GPX bytes.

        Uses the same approach as KomootGPX: fetches the full tour JSON
        with embedded coordinates and compiles to GPX.
        """
        tour_data = self._api.fetch_tour(str(tour_id))
        compiler = GpxCompiler(tour_data, self._api, no_poi=True)
        gpx_xml = compiler.generate()
        return gpx_xml.encode("utf-8")

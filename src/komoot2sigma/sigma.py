"""Sigma Data Cloud API client - OAuth2 auth and route upload."""

from __future__ import annotations

import io
import time
import urllib.parse
import uuid
import zipfile
from xml.etree.ElementTree import Element, SubElement, tostring
from typing import Any

import click
import gpxpy
import gpxpy.gpx
import requests

from komoot2sigma.config import save_sigma_credentials

SIGMA_CLOUD_BASE = "https://www.sigma-data-cloud.com"
OAUTH_AUTHORIZE_URL = f"{SIGMA_CLOUD_BASE}/oauth/authorize"
OAUTH_TOKEN_URL = f"{SIGMA_CLOUD_BASE}/oauth/token"
LOGIN_FORM_URL = f"{SIGMA_CLOUD_BASE}/login.do"
SYNC_PATH = "/sync"
UPLOAD_TRACK_PATH = "/sync/upload/track"

CLIENT_ID = "dfed306c44564f565065687d534d6ec1"
CLIENT_SECRET = "edffc4f97ed15a2c2ae4ea49d545e42c"
REDIRECT_URI = "https://www.sigma-dc-control.com"
SCOPE = "write"

KOMOOT_GUID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


class SigmaCloudClient:
    """Client for the Sigma Data Cloud sync API."""

    def __init__(self, access_token: str | None = None):
        self._session = requests.Session()
        self._access_token = access_token

    def authenticate(self, email: str, password: str) -> str:
        """Authenticate with Sigma Cloud using headless OAuth2 form-POST flow.

        Performs the full authorization code grant without opening a browser:
        1. GET /oauth/authorize to establish session
        2. POST /login.do with credentials
        3. Follow redirects to capture authorization code
        4. Exchange code for access token

        Returns the access_token.
        """
        session = requests.Session()

        authorize_params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
        }
        resp = session.get(
            OAUTH_AUTHORIZE_URL,
            params=authorize_params,
            allow_redirects=False,
        )

        # Follow redirects manually until we reach the login page
        while resp.status_code in (301, 302, 303, 307):
            location = resp.headers["Location"]
            if not location.startswith("http"):
                location = f"{SIGMA_CLOUD_BASE}{location}"
            resp = session.get(location, allow_redirects=False)

        # POST credentials to the login form
        login_resp = session.post(
            LOGIN_FORM_URL,
            data={"j_username": email, "j_password": password},
            allow_redirects=False,
        )

        if login_resp.status_code not in (301, 302, 303, 307):
            raise click.ClickException(
                "Sigma login failed. Check your email and password."
            )

        # Follow the post-login redirect back to the authorize endpoint
        location = login_resp.headers["Location"]
        if not location.startswith("http"):
            location = f"{SIGMA_CLOUD_BASE}{location}"

        auth_resp = session.get(location, allow_redirects=False)

        # The authorize endpoint may redirect again — keep following until
        # we get the redirect to the redirect_uri with the code parameter
        while auth_resp.status_code in (301, 302, 303, 307):
            redirect_location = auth_resp.headers["Location"]
            if redirect_location.startswith(REDIRECT_URI):
                break
            if not redirect_location.startswith("http"):
                redirect_location = f"{SIGMA_CLOUD_BASE}{redirect_location}"
            auth_resp = session.get(redirect_location, allow_redirects=False)
        else:
            raise click.ClickException(
                f"Sigma authorization failed: did not receive auth code "
                f"(HTTP {auth_resp.status_code})."
            )

        # Extract the authorization code from the final redirect Location
        final_location = auth_resp.headers["Location"]
        parsed = urllib.parse.urlparse(final_location)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" not in params:
            error = params.get("error_description", params.get("error", ["unknown"]))
            raise click.ClickException(
                f"Sigma authorization failed: {error[0]}"
            )

        auth_code = params["code"][0]
        return self._exchange_code(auth_code)

    def _exchange_code(self, code: str) -> str:
        """Exchange authorization code for access token using Basic Auth."""
        response = self._session.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
        )

        if response.status_code != 200:
            raise click.ClickException(
                f"Token exchange failed (HTTP {response.status_code}): "
                f"{response.text}"
            )

        token_data = response.json()
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in")

        save_sigma_credentials(access_token, refresh_token, expires_in)
        self._access_token = access_token

        return access_token

    def refresh_access_token(self, refresh_token: str) -> str:
        """Attempt to refresh the access token using a refresh token."""
        response = self._session.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(CLIENT_ID, CLIENT_SECRET),
        )

        if response.status_code != 200:
            raise click.ClickException(
                "Token refresh failed. Please re-authorize with "
                "`komoot2sigma login sigma --email <email> --password <pass>`."
            )

        token_data = response.json()
        access_token = token_data["access_token"]
        new_refresh = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in")

        save_sigma_credentials(access_token, new_refresh, expires_in)
        self._access_token = access_token
        return access_token

    def _ensure_token(self) -> str:
        """Return the current access token or raise if not set."""
        if not self._access_token:
            raise click.ClickException(
                "Not authenticated with Sigma Cloud. "
                "Run `komoot2sigma login sigma` first."
            )
        return self._access_token

    def list_route_guids(self, verbose: bool = False) -> set[str]:
        """Query Sigma Cloud for all existing route GUIDs.

        Uses the /sync endpoint with an empty list and lastSync=0 to get
        the server's full inventory of routes (TRACK type).
        """
        token = self._ensure_token()
        sync_url = f"{SIGMA_CLOUD_BASE}{SYNC_PATH}?access_token={token}"

        request_body = {
            "dataList": {
                "type": "TRACK",
                "lastSync": 0,
                "list": [],
            }
        }

        try:
            response = self._session.post(
                sync_url,
                json=request_body,
                headers={"Content-Type": "application/json"},
            )
            if verbose:
                click.echo(
                    f"  Sync response: {response.status_code} "
                    f"{response.text[:300]}"
                )
            if response.status_code != 200:
                return set()

            data = response.json()
            guids: set[str] = set()
            for data_list in data.get("dataLists", []):
                for item in data_list.get("dataList", []):
                    guid = item.get("GUID")
                    if guid:
                        guids.add(guid.upper())
            return guids
        except (requests.RequestException, ValueError) as exc:
            if verbose:
                click.echo(f"  Sync query error: {exc}")
            return set()

    def upload_gpx(
        self,
        gpx_content: bytes,
        name: str,
        verbose: bool = False,
        route_guid: str | None = None,
    ) -> bool:
        """Upload a GPX route to Sigma Cloud.

        Converts GPX to zipped STF (Sigma Track File) format and uploads
        via multipart form matching the SIGMA RIDE app protocol.
        Falls back to raw GPX upload if STF conversion fails.

        If route_guid is provided, it is used as the GUID for the route
        (enables deduplication via deterministic IDs).
        """
        token = self._ensure_token()

        if verbose:
            click.echo("  Converting GPX to STF format...")

        if self._try_stf_upload(gpx_content, name, token, verbose, route_guid):
            return True

        if verbose:
            click.echo("  STF upload failed, trying raw GPX...")

        gpx_cleaned = strip_waypoints_from_gpx(gpx_content)
        return self._try_gpx_upload(gpx_cleaned, name, token, verbose, route_guid)

    def _try_gpx_upload(
        self,
        gpx_content: bytes,
        name: str,
        token: str,
        verbose: bool = False,
        route_guid: str | None = None,
    ) -> bool:
        """Upload GPX as multipart form with full metadata fields.

        Matches the Sigma RIDE APK's SigmaCloudSyncTracks.uploadRequest():
        access_token, GUID, modificationDate, dataType, name,
        latitude, longitude, distance, altitudeUp, altitudeDown, + file.
        """
        route_meta = _extract_gpx_metadata(gpx_content)

        upload_url = (
            f"{SIGMA_CLOUD_BASE}{UPLOAD_TRACK_PATH}"
            f"?access_token={token}"
        )

        if route_guid is None:
            route_guid = str(uuid.uuid4()).upper()
        modification_date = str(int(time.time() * 1000))

        form_fields = {
            "access_token": token,
            "GUID": route_guid,
            "modificationDate": modification_date,
            "dataType": "Route",
            "name": name,
            "latitude": str(route_meta["latitude"]),
            "longitude": str(route_meta["longitude"]),
            "distance": str(route_meta["distance"]),
            "altitudeUp": str(route_meta["altitude_up"]),
            "altitudeDown": str(route_meta["altitude_down"]),
        }

        files = {
            "file": (f"{name}.gpx", gpx_content, "application/gpx+xml"),
        }

        try:
            response = self._session.post(
                upload_url, data=form_fields, files=files
            )
            if verbose:
                click.echo(
                    f"  GPX upload response: {response.status_code} "
                    f"{response.text[:200]}"
                )
            return response.status_code in (200, 201, 204)
        except requests.RequestException as exc:
            if verbose:
                click.echo(f"  GPX upload error: {exc}")
            return False

    def _try_stf_upload(
        self,
        gpx_content: bytes,
        name: str,
        token: str,
        verbose: bool = False,
        route_guid: str | None = None,
    ) -> bool:
        """Convert GPX to zipped STF and upload with full metadata.

        This matches the SIGMA RIDE app's native upload protocol:
        - Convert GPX → STF XML
        - ZIP the STF file
        - Multipart POST with metadata fields + zipped file
        """
        stf_data = gpx_to_stf(gpx_content, name, route_guid)
        if stf_data is None:
            if verbose:
                click.echo("  Failed to convert GPX to STF format.")
            return False

        route_guid = stf_data["guid"]
        stf_xml = stf_data["xml"]
        meta = stf_data["metadata"]

        # ZIP the STF XML (as the app does via ZipUtil.generateZIPFile)
        zip_buffer = io.BytesIO()
        stf_filename = f"track{route_guid}.stf"
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(stf_filename, stf_xml)
        zip_bytes = zip_buffer.getvalue()

        upload_url = (
            f"{SIGMA_CLOUD_BASE}{UPLOAD_TRACK_PATH}"
            f"?access_token={token}"
        )

        modification_date = str(int(time.time() * 1000))

        form_fields = {
            "access_token": token,
            "GUID": route_guid,
            "modificationDate": modification_date,
            "dataType": "Route",
            "name": name,
            "latitude": str(meta["latitude"]),
            "longitude": str(meta["longitude"]),
            "distance": str(meta["distance"]),
            "altitudeUp": str(meta["altitude_up"]),
            "altitudeDown": str(meta["altitude_down"]),
        }

        files = {
            "file": (
                f"track{route_guid}.zip",
                zip_bytes,
                "application/zip",
            ),
        }

        try:
            response = self._session.post(
                upload_url, data=form_fields, files=files
            )
            if verbose:
                click.echo(
                    f"  STF upload response: {response.status_code} "
                    f"{response.text[:200]}"
                )
            return response.status_code in (200, 201, 204)
        except requests.RequestException as exc:
            if verbose:
                click.echo(f"  STF upload error: {exc}")
            return False


def gpx_to_stf(
    gpx_content: bytes, name: str, route_guid: str | None = None
) -> dict[str, Any] | None:
    """Convert GPX to Sigma Track File (STF) XML format.

    Based on the format used by SIGMA RIDE app and confirmed by
    https://github.com/the666bbq/gpx2stf

    Returns dict with 'guid', 'xml' (str), and 'metadata' (dict).
    """
    try:
        gpx = gpxpy.parse(gpx_content.decode("utf-8"))
    except Exception:
        return None

    all_points: list[gpxpy.gpx.GPXTrackPoint] = []
    for track in gpx.tracks:
        for segment in track.segments:
            all_points.extend(segment.points)

    if not all_points and gpx.routes:
        for route in gpx.routes:
            all_points.extend(route.points)

    if not all_points:
        return None

    # Calculate metadata
    total_distance = 0.0
    elevation_up = 0.0
    elevation_down = 0.0
    prev_elevation: float | None = None

    for idx, point in enumerate(all_points):
        if prev_elevation is not None and point.elevation is not None:
            diff = point.elevation - prev_elevation
            if diff > 0:
                elevation_up += diff
            else:
                elevation_down += abs(diff)

        if point.elevation is not None:
            prev_elevation = point.elevation

        if idx > 0:
            prev_pt = all_points[idx - 1]
            total_distance += point.distance_2d(prev_pt) or 0.0

    first_pt = all_points[0]
    last_pt = all_points[-1]
    if route_guid is None:
        route_guid = str(uuid.uuid4()).upper()
    modification_date = str(int(time.time() * 1000))

    # Check if circuit (start ~= end, within ~100m)
    is_circuit = 0
    if first_pt.distance_2d(last_pt) is not None:
        if (first_pt.distance_2d(last_pt) or 0) < 100:
            is_circuit = 1

    # Build STF XML
    root = Element("Route")
    SubElement(root, "GUID").text = route_guid
    SubElement(root, "modificationDate").text = modification_date
    SubElement(root, "latitudeStart").text = f"{first_pt.latitude:.7f}"
    SubElement(root, "longitudeStart").text = f"{first_pt.longitude:.7f}"
    SubElement(root, "latitudeEnd").text = f"{last_pt.latitude:.7f}"
    SubElement(root, "longitudeEnd").text = f"{last_pt.longitude:.7f}"
    SubElement(root, "name").text = name
    SubElement(root, "description").text = ""
    SubElement(root, "distance").text = str(int(total_distance))
    SubElement(root, "altitudeDifferencesUphill").text = str(int(elevation_up))
    SubElement(root, "altitudeDifferencesDownhill").text = str(
        int(elevation_down)
    )
    SubElement(root, "rating").text = "0"
    SubElement(root, "autor").text = ""
    SubElement(root, "webPortalId").text = ""
    SubElement(root, "downloadId").text = "0"
    SubElement(root, "ghostData")
    SubElement(root, "isCircuit").text = str(is_circuit)
    SubElement(root, "creationTimestamp").text = modification_date
    SubElement(root, "trackStatus").text = "none"

    route_points = SubElement(root, "RoutePoints")
    for point in all_points:
        rp = SubElement(route_points, "RoutePoint")
        SubElement(rp, "latitude").text = f"{point.latitude:.7f}"
        SubElement(rp, "longitude").text = f"{point.longitude:.7f}"
        altitude = int(point.elevation) if point.elevation else 0
        SubElement(rp, "altitude").text = str(altitude)
        SubElement(rp, "userPoint").text = "1"
        SubElement(rp, "routingType").text = "imported"
        SubElement(rp, "direction").text = "0"
        SubElement(rp, "street").text = ""
        SubElement(rp, "useForTrack").text = "1"

    SubElement(root, "RoutePOIs")

    xml_str = tostring(root, encoding="unicode", xml_declaration=False)

    return {
        "guid": route_guid,
        "xml": xml_str,
        "metadata": {
            "latitude": first_pt.latitude,
            "longitude": first_pt.longitude,
            "distance": int(total_distance),
            "altitude_up": int(elevation_up),
            "altitude_down": int(elevation_down),
        },
    }


def _extract_gpx_metadata(gpx_content: bytes) -> dict[str, float]:
    """Extract route metadata from GPX for the upload form fields."""
    defaults: dict[str, float] = {
        "latitude": 0.0,
        "longitude": 0.0,
        "distance": 0.0,
        "altitude_up": 0,
        "altitude_down": 0,
    }

    try:
        gpx = gpxpy.parse(gpx_content.decode("utf-8"))
    except Exception:
        return defaults

    all_points: list[gpxpy.gpx.GPXTrackPoint] = []
    for track in gpx.tracks:
        for segment in track.segments:
            all_points.extend(segment.points)

    if not all_points and gpx.routes:
        for route in gpx.routes:
            all_points.extend(route.points)

    if not all_points:
        return defaults

    first_point = all_points[0]
    total_distance = 0.0
    elevation_up = 0.0
    elevation_down = 0.0
    prev_elevation: float | None = None

    for idx, point in enumerate(all_points):
        if prev_elevation is not None and point.elevation is not None:
            diff = point.elevation - prev_elevation
            if diff > 0:
                elevation_up += diff
            else:
                elevation_down += abs(diff)

        if point.elevation is not None:
            prev_elevation = point.elevation

        if idx > 0:
            prev_pt = all_points[idx - 1]
            total_distance += point.distance_2d(prev_pt) or 0.0

    return {
        "latitude": first_point.latitude,
        "longitude": first_point.longitude,
        "distance": round(total_distance, 1),
        "altitude_up": int(elevation_up),
        "altitude_down": int(elevation_down),
    }


def guid_for_komoot_tour(tour_id: str) -> str:
    """Generate a deterministic GUID for a Komoot tour ID.

    Uses UUID5 with a fixed namespace so the same tour always maps to
    the same Sigma GUID, enabling deduplication.
    """
    return str(uuid.uuid5(KOMOOT_GUID_NAMESPACE, tour_id)).upper()


def strip_waypoints_from_gpx(gpx_content: bytes) -> bytes:
    """Remove waypoints/POIs from GPX, keeping only tracks and routes."""
    try:
        gpx = gpxpy.parse(gpx_content.decode("utf-8"))
    except Exception:
        return gpx_content

    gpx.waypoints = []
    return gpx.to_xml().encode("utf-8")

"""Google Photos Picker API client.

The Picker API returns temporary media URLs. This client only uses them while
importing selected media and never returns them as gallery URLs.
"""
import time
from typing import Any, Dict, Iterable
from urllib.parse import quote

import httpx

from src.file_sources import FileSourceError, GOOGLE_PHOTOS_PICKER_URL, google_oauth_credentials, save_source


PICKER_SCOPE = "https://www.googleapis.com/auth/photospicker.mediaitems.readonly"


class GooglePhotosProvider:
    def __init__(self, source: Dict[str, Any]):
        self.source = source

    def _token(self) -> str:
        oauth = self.source.get("oauth") or {}
        if oauth.get("refresh_token") and oauth.get("expires_at", 0) <= time.time() + 30:
            client_id, client_secret = google_oauth_credentials()
            response = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={"refresh_token": oauth["refresh_token"], "client_id": client_id,
                      "client_secret": client_secret, "grant_type": "refresh_token"},
                timeout=30,
            )
            if response.status_code != 200:
                raise FileSourceError("Google Photos authorization expired; reconnect the integration")
            refreshed = response.json()
            oauth["access_token"] = refreshed.get("access_token", "")
            oauth["expires_at"] = time.time() + int(refreshed.get("expires_in", 0))
            self.source["oauth"] = oauth
            save_source(self.source)
        token = oauth.get("access_token")
        if not token:
            raise FileSourceError("Google Photos integration needs authorization")
        return token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}"}

    def create_session(self) -> Dict[str, Any]:
        response = httpx.post(
            f"{GOOGLE_PHOTOS_PICKER_URL}/sessions",
            headers=self._headers(),
            json={"pickingConfig": {"maxItemCount": 2000}},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise FileSourceError("Google Photos authorization was revoked; reconnect the integration")
        response.raise_for_status()
        data = response.json()
        if not data.get("id") or not data.get("pickerUri"):
            raise FileSourceError("Google Photos returned an invalid Picker session")
        return data

    def get_session(self, google_session_id: str) -> Dict[str, Any]:
        response = httpx.get(
            f"{GOOGLE_PHOTOS_PICKER_URL}/sessions/{quote(google_session_id, safe='')}",
            headers=self._headers(), timeout=30,
        )
        if response.status_code in (401, 403):
            raise FileSourceError("Google Photos authorization was revoked; reconnect the integration")
        if response.status_code == 404:
            raise FileSourceError("Google Photos Picker session has expired")
        response.raise_for_status()
        return response.json()

    def list_media_items(self, google_session_id: str) -> Iterable[Dict[str, Any]]:
        response = httpx.get(
            f"{GOOGLE_PHOTOS_PICKER_URL}/mediaItems",
            params={"sessionId": google_session_id}, headers=self._headers(), timeout=30,
        )
        if response.status_code in (401, 403):
            raise FileSourceError("Google Photos authorization was revoked; reconnect the integration")
        response.raise_for_status()
        return response.json().get("mediaItems", [])

    def download(self, base_url: str, destination: str, *, is_video: bool = False) -> None:
        if not base_url or not base_url.startswith("https://lh3.googleusercontent.com/"):
            raise FileSourceError("Google Photos returned an invalid temporary media URL")
        suffix = "=dv" if is_video else "=d"
        with httpx.stream("GET", base_url + suffix, headers=self._headers(), timeout=120,
                          follow_redirects=False) as response:
            response.raise_for_status()
            with open(destination, "wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
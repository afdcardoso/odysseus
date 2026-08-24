"""Read-only, owner-scoped remote file sources.

This deliberately does not reuse ``src.integrations``: API integrations are
operator-managed tools, while file sources contain a user's private documents.
"""
import json
import os
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx

from core.atomic_io import atomic_write_json
from core.platform_compat import safe_chmod
from src.constants import DATA_DIR, FILE_SOURCES_FILE
from src.secret_storage import decrypt, encrypt
from src.url_safety import check_outbound_url

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_URL = "https://www.googleapis.com/drive/v3"


class FileSourceError(ValueError):
    pass


def google_oauth_credentials() -> tuple[str, str]:
    """Return the deployment-managed Google OAuth client credentials."""
    return (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip(),
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip(),
    )


def _read() -> Dict[str, Any]:
    try:
        with open(FILE_SOURCES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"sources": [], "oauth_states": []}
    except (OSError, json.JSONDecodeError):
        return {"sources": [], "oauth_states": []}


def _write(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(FILE_SOURCES_FILE), exist_ok=True)
    atomic_write_json(FILE_SOURCES_FILE, data, indent=2)
    safe_chmod(FILE_SOURCES_FILE, 0o600)


def _decode(source: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(source)
    for key in ("credentials", "oauth"):
        if item.get(key):
            try:
                item[key] = json.loads(decrypt(item[key]))
            except Exception:
                item[key] = {}
    # Client credentials are deployment configuration, not source data. Drop
    # them from legacy Google records when they are read.
    if item.get("provider") == "google_drive":
        item.pop("credentials", None)
    return item


def _encode(source: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(source)
    for key in ("credentials", "oauth"):
        value = item.get(key)
        if isinstance(value, dict):
            item[key] = encrypt(json.dumps(value, separators=(",", ":")))
    return item


def public_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """A source representation that is safe to return to a browser."""
    return {key: source.get(key) for key in (
        "id", "provider", "display_name", "root_path", "root_url", "created_at",
    )}


def list_sources(owner: str) -> List[Dict[str, Any]]:
    return [public_source(_decode(x)) for x in _read().get("sources", []) if x.get("owner") == owner]


def get_source(owner: str, source_id: str) -> Optional[Dict[str, Any]]:
    for source in _read().get("sources", []):
        if source.get("id") == source_id and source.get("owner") == owner:
            return _decode(source)
    return None


def save_source(source: Dict[str, Any]) -> Dict[str, Any]:
    data = _read()
    rows = data.setdefault("sources", [])
    encoded = _encode(source)
    for index, row in enumerate(rows):
        if row.get("id") == source["id"]:
            rows[index] = encoded
            break
    else:
        rows.append(encoded)
    _write(data)
    return source


def delete_source(owner: str, source_id: str) -> bool:
    data = _read()
    before = len(data.get("sources", []))
    data["sources"] = [x for x in data.get("sources", [])
                       if not (x.get("id") == source_id and x.get("owner") == owner)]
    if len(data["sources"]) != before:
        _write(data)
        return True
    return False


def create_source(owner: str, provider: str, display_name: str, **fields: Any) -> Dict[str, Any]:
    if provider not in ("webdav", "google_drive"):
        raise FileSourceError("Unsupported file-source provider")
    return save_source({"id": uuid.uuid4().hex, "owner": owner, "provider": provider,
                        "display_name": (display_name or provider).strip()[:120],
                        "created_at": int(time.time()), **fields})


def create_oauth_state(owner: str, source_id: str) -> str:
    data = _read()
    state = uuid.uuid4().hex + uuid.uuid4().hex
    rows = [x for x in data.get("oauth_states", []) if x.get("expires_at", 0) > time.time()]
    rows.append({"state": encrypt(state), "owner": owner, "source_id": source_id,
                 "expires_at": time.time() + 600})
    data["oauth_states"] = rows
    _write(data)
    return state


def consume_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    data = _read(); found = None; keep = []
    for row in data.get("oauth_states", []):
        valid = row.get("expires_at", 0) > time.time()
        try: matches = valid and decrypt(row.get("state", "")) == state
        except Exception: matches = False
        if matches and found is None: found = row
        elif valid: keep.append(row)
    data["oauth_states"] = keep
    _write(data)
    return found


def validate_webdav_url(root_url: str) -> str:
    parsed = urlparse((root_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
        raise FileSourceError("WebDAV root URL must be an HTTP(S) URL without query or fragment")
    block_private = os.getenv("INTEGRATION_API_BLOCK_PRIVATE_IPS", "false").lower() == "true"
    ok, reason = check_outbound_url(root_url, block_private=block_private)
    if not ok:
        raise FileSourceError(f"WebDAV URL rejected: {reason}")
    return root_url.rstrip("/") + "/"


@dataclass
class RemoteFile:
    id: str
    name: str
    path: str
    is_folder: bool
    size: Optional[int] = None
    mime_type: str = ""
    modified_time: str = ""
    export_mime: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class WebDAVProvider:
    def __init__(self, source: Dict[str, Any]):
        self.source = source
        self.root = validate_webdav_url(source.get("root_url", ""))
        credentials = source.get("credentials") or {}
        self.auth = (credentials.get("username", ""), credentials.get("password", ""))

    def _url(self, path: str) -> str:
        path = (path or "").lstrip("/")
        url = urljoin(self.root, quote(path, safe="/%"))
        if not url.startswith(self.root):
            raise FileSourceError("Path escapes configured WebDAV root")
        return url

    def validate(self) -> None:
        response = httpx.request("PROPFIND", self.root, auth=self.auth,
                                 headers={"Depth": "0"}, timeout=15, follow_redirects=False)
        if response.status_code not in (200, 207):
            raise FileSourceError(f"WebDAV connection failed ({response.status_code})")

    def list_folder(self, path: str = "", page_token: str = "") -> Dict[str, Any]:
        if page_token: raise FileSourceError("WebDAV servers do not support cursor pagination")
        response = httpx.request("PROPFIND", self._url(path), auth=self.auth,
                                 headers={"Depth": "1"}, timeout=30, follow_redirects=False)
        if response.status_code != 207: raise FileSourceError(f"WebDAV listing failed ({response.status_code})")
        ns = {"d": "DAV:"}; files = []
        for item in ET.fromstring(response.content).findall("d:response", ns):
            href = item.findtext("d:href", "", ns)
            prop = item.find("d:propstat/d:prop", ns)
            if prop is None: continue
            collection = prop.find("d:resourcetype/d:collection", ns) is not None
            relative = href.split("?", 1)[0].rstrip("/")
            root_path = urlparse(self.root).path.rstrip("/")
            if root_path and relative.startswith(root_path): relative = relative[len(root_path):]
            relative = relative.lstrip("/")
            if relative == (path or "").strip("/"): continue
            name = PurePosixPath(relative).name
            size = prop.findtext("d:getcontentlength", None, ns)
            files.append(RemoteFile(relative, name, relative, collection, int(size) if size and size.isdigit() else None,
                                    prop.findtext("d:getcontenttype", "", ns)).as_dict())
        return {"files": files, "next_page_token": None}

    def download(self, remote: RemoteFile, destination: str) -> None:
        with httpx.stream("GET", self._url(remote.path), auth=self.auth, timeout=60, follow_redirects=False) as response:
            response.raise_for_status()
            with open(destination, "wb") as output:
                for chunk in response.iter_bytes(): output.write(chunk)


class GoogleDriveProvider:
    EXPORTS = {"application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
               "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
               "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx")}
    def __init__(self, source: Dict[str, Any]): self.source = source
    def _token(self) -> str:
        oauth = self.source.get("oauth") or {}
        # Refresh a little early; OAuth access tokens are short-lived and the
        # refresh token remains encrypted with the rest of the source record.
        if oauth.get("refresh_token") and oauth.get("expires_at", 0) <= time.time() + 30:
            client_id, client_secret = google_oauth_credentials()
            response = httpx.post(GOOGLE_TOKEN_URL, data={"refresh_token": oauth["refresh_token"],
                "client_id": client_id, "client_secret": client_secret,
                "grant_type": "refresh_token"}, timeout=30)
            if response.status_code != 200: raise FileSourceError("Google Drive token refresh failed; reconnect the source")
            refreshed = response.json(); oauth["access_token"] = refreshed.get("access_token", "")
            oauth["expires_at"] = time.time() + int(refreshed.get("expires_in", 0))
            self.source["oauth"] = oauth
            save_source(self.source)
        token = oauth.get("access_token")
        if not token: raise FileSourceError("Google Drive connection needs authorization")
        return token
    def _headers(self): return {"Authorization": f"Bearer {self._token()}"}
    def validate(self) -> None:
        response = httpx.get(f"{GOOGLE_DRIVE_URL}/about", params={"fields": "user"}, headers=self._headers(), timeout=20)
        if response.status_code != 200: raise FileSourceError("Google Drive authorization failed")
    def list_folder(self, path: str = "root", page_token: str = "") -> Dict[str, Any]:
        parent = path or "root"; query = f"'{parent}' in parents and trashed = false"
        params = {"q": query, "pageSize": 100, "fields": "nextPageToken,files(id,name,mimeType,size,modifiedTime)"}
        if page_token: params["pageToken"] = page_token
        response = httpx.get(f"{GOOGLE_DRIVE_URL}/files", params=params, headers=self._headers(), timeout=30); response.raise_for_status()
        data = response.json(); files = []
        for x in data.get("files", []):
            mime = x.get("mimeType", ""); export = self.EXPORTS.get(mime)
            name = x.get("name", "file") + (export[1] if export else "")
            files.append(RemoteFile(x["id"], name, x["id"], mime == "application/vnd.google-apps.folder", x.get("size"), mime, x.get("modifiedTime", ""), export[0] if export else "").as_dict())
        return {"files": files, "next_page_token": data.get("nextPageToken")}
    def download(self, remote: RemoteFile, destination: str) -> None:
        if remote.export_mime:
            response = httpx.get(f"{GOOGLE_DRIVE_URL}/files/{quote(remote.id)}/export", params={"mimeType": remote.export_mime}, headers=self._headers(), timeout=60)
        else: response = httpx.get(f"{GOOGLE_DRIVE_URL}/files/{quote(remote.id)}", params={"alt": "media"}, headers=self._headers(), timeout=60)
        response.raise_for_status(); open(destination, "wb").write(response.content)


def provider_for(source: Dict[str, Any]):
    return WebDAVProvider(source) if source.get("provider") == "webdav" else GoogleDriveProvider(source)

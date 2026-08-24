"""HTTP API for user-owned read-only remote document sources."""
import asyncio
import os
import shutil
import uuid
from typing import Any, Dict
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from routes.personal_routes import _personal_upload_dir_for_owner, _unique_personal_upload_path
from src.auth_helpers import require_privilege
from src.file_sources import (GOOGLE_AUTH_URL, GOOGLE_TOKEN_URL, FileSourceError,
    RemoteFile, consume_oauth_state, create_oauth_state, create_source, delete_source,
    get_source, google_oauth_credentials, list_sources, provider_for, public_source,
    save_source, validate_webdav_url)
from src.markitdown_runtime import MARKITDOWN_EXTS, MARKITDOWN_MISSING, load_markitdown
from src.personal_docs import extract_office_text, extract_pdf_text, read_text_file
from src.upload_limits import PERSONAL_UPLOAD_MAX_BYTES, format_byte_limit


def setup_file_source_routes(rag_manager: Any):
    router = APIRouter(prefix="/api/file-sources", tags=["file-sources"])
    import_lock = asyncio.Lock()

    def google_callback_url(request: Request) -> str:
        return os.environ.get("GOOGLE_DRIVE_OAUTH_REDIRECT_URI", "").strip() or str(
            request.url_for("file_source_google_callback")
        )

    def document_owner(request: Request) -> str:
        return require_privilege(request, "can_use_documents")

    def source_or_404(owner: str, source_id: str) -> Dict[str, Any]:
        source = get_source(owner, source_id)
        if not source: raise HTTPException(404, "File source not found")
        return source

    @router.get("")
    def sources(owner: str = Depends(document_owner)):
        return {"sources": list_sources(owner)}

    @router.post("/webdav/test")
    async def test_webdav(payload: Dict[str, Any] = Body(...), owner: str = Depends(document_owner)):
        try:
            root_url = validate_webdav_url(str(payload.get("root_url", "")))
            temporary = {"provider": "webdav", "root_url": root_url,
                         "credentials": {"username": str(payload.get("username", "")), "password": str(payload.get("password", ""))}}
            await run_in_threadpool(provider_for(temporary).validate)
            return {"ok": True}
        except FileSourceError as exc: raise HTTPException(400, str(exc))
        except httpx.HTTPError as exc: raise HTTPException(400, f"WebDAV connection failed: {exc}")

    @router.post("/webdav")
    async def connect_webdav(payload: Dict[str, Any] = Body(...), owner: str = Depends(document_owner)):
        try:
            root_url = validate_webdav_url(str(payload.get("root_url", "")))
            source = create_source(owner, "webdav", str(payload.get("display_name") or "WebDAV"), root_url=root_url,
                root_path="", credentials={"username": str(payload.get("username", "")), "password": str(payload.get("password", ""))})
            try:
                await run_in_threadpool(provider_for(source).validate)
            except Exception:
                delete_source(owner, source["id"])
                raise
            return {"source": public_source(source)}
        except FileSourceError as exc: raise HTTPException(400, str(exc))
        except httpx.HTTPError as exc: raise HTTPException(400, f"WebDAV connection failed: {exc}")

    @router.post("/google/start")
    def google_start(request: Request, payload: Dict[str, Any] = Body(...), owner: str = Depends(document_owner)):
        client_id, client_secret = google_oauth_credentials()
        if not client_id or not client_secret: raise HTTPException(400, "Google OAuth client ID and secret are required")
        source = create_source(owner, "google_drive", str(payload.get("display_name") or "Google Drive"), root_path="root",
                               oauth={})
        state = create_oauth_state(owner, source["id"])
        callback = google_callback_url(request)
        query = urlencode({"client_id": client_id, "redirect_uri": callback, "response_type": "code", "scope": "https://www.googleapis.com/auth/drive.readonly", "access_type": "offline", "prompt": "consent", "state": state})
        return {"authorization_url": f"{GOOGLE_AUTH_URL}?{query}", "source": public_source(source)}

    @router.get("/google/callback", name="file_source_google_callback")
    async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        row = consume_oauth_state(state)
        if not row: raise HTTPException(400, "Google authorization state is invalid or expired")
        if error or not code: return RedirectResponse("/?file_source_error=" + (error or "authorization_denied"), status_code=303)
        source = get_source(row["owner"], row["source_id"])
        if not source: raise HTTPException(404, "File source no longer exists")
        client_id, client_secret = google_oauth_credentials()
        if not client_id or not client_secret:
            raise HTTPException(400, "Google OAuth client ID and secret are not configured")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(GOOGLE_TOKEN_URL, data={"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": google_callback_url(request), "grant_type": "authorization_code"})
            response.raise_for_status(); tokens = response.json()
        except httpx.HTTPError as exc: raise HTTPException(400, f"Google token exchange failed: {exc}")
        source["oauth"] = {"access_token": tokens.get("access_token", ""), "refresh_token": tokens.get("refresh_token", ""), "expires_at": __import__("time").time() + int(tokens.get("expires_in", 0))}
        save_source(source)
        return RedirectResponse("/?file_source_connected=google_drive", status_code=303)

    @router.get("/{source_id}/browse")
    async def browse(source_id: str, path: str = "", page_token: str = "", owner: str = Depends(document_owner)):
        try: return await run_in_threadpool(provider_for(source_or_404(owner, source_id)).list_folder, path, page_token)
        except FileSourceError as exc: raise HTTPException(400, str(exc))
        except httpx.HTTPError as exc: raise HTTPException(502, f"Remote listing failed: {exc}")

    @router.post("/{source_id}/import")
    async def import_files(source_id: str, payload: Dict[str, Any] = Body(...), owner: str = Depends(document_owner)):
        source = source_or_404(owner, source_id); entries = payload.get("files")
        if not isinstance(entries, list) or not entries: raise HTTPException(400, "Select at least one file")
        rag = rag_manager
        if not rag: raise HTTPException(503, "RAG system is not available")
        results = []
        async with import_lock:
            for raw in entries:
                if not isinstance(raw, dict):
                    results.append({"name": "file", "error": "Invalid file selection"}); continue
                remote = RemoteFile(str(raw.get("id", "")), str(raw.get("name", "")), str(raw.get("path", "")),
                                    bool(raw.get("is_folder", False)), raw.get("size"), str(raw.get("mime_type", "")),
                                    str(raw.get("modified_time", "")), str(raw.get("export_mime", "")))
                if not remote.id or remote.is_folder: results.append({"name": remote.name or "file", "error": "Invalid file selection"}); continue
                ext = os.path.splitext(remote.name)[1].lower()
                if ext not in {".txt", ".md", ".json", ".csv", ".html", ".htm", ".pdf", *MARKITDOWN_EXTS}:
                    results.append({"name": remote.name, "error": "This file type is not supported for document indexing"}); continue
                if ext in MARKITDOWN_EXTS and ext != ".docx":
                    try: load_markitdown()
                    except RuntimeError: results.append({"name": remote.name, "error": MARKITDOWN_MISSING}); continue
                directory = os.path.join(_personal_upload_dir_for_owner(owner), "connected-files", source_id)
                os.makedirs(directory, exist_ok=True)
                path, stored, safe = _unique_personal_upload_path(directory, remote.name)
                try:
                    await run_in_threadpool(provider_for(source).download, remote, path)
                    if os.path.getsize(path) > PERSONAL_UPLOAD_MAX_BYTES:
                        os.remove(path); results.append({"name": safe, "error": f"File exceeds {format_byte_limit(PERSONAL_UPLOAD_MAX_BYTES)} limit"}); continue
                    def index_one():
                        if ext == ".pdf": text = extract_pdf_text(path)
                        elif ext in MARKITDOWN_EXTS: text = extract_office_text(path)
                        else: text = read_text_file(path)
                        if not text.strip(): raise FileSourceError("No extractable text in file")
                        count = 0
                        for index, chunk in enumerate(rag._split_into_chunks(text, chunk_size=500)):
                            metadata = {"source": path, "filename": safe, "stored_filename": stored, "directory": directory, "type": ext, "chunk_id": index, "owner": owner, "file_source_id": source_id, "file_source_provider": source["provider"], "remote_file_id": remote.id, "remote_path": remote.path}
                            count += bool(rag.add_document(chunk, metadata))
                        return count
                    count = await run_in_threadpool(index_one)
                    results.append({"name": safe, "indexed_count": count})
                except Exception as exc:
                    try: os.remove(path)
                    except OSError: pass
                    results.append({"name": safe, "error": str(exc)})
        return {"results": results, "imported_count": sum(1 for x in results if "indexed_count" in x)}

    @router.delete("/{source_id}")
    def disconnect(source_id: str, owner: str = Depends(document_owner)):
        if not delete_source(owner, source_id): raise HTTPException(404, "File source not found")
        return {"ok": True, "message": "Disconnected. Imported local files were kept."}
    return router

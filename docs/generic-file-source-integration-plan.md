# Generic File-Source Integration Plan

## Summary

Add a separate, user-owned file-source system for read-only Google Drive and WebDAV NAS connections. Users browse remote folders and import selected files as private local copies, which are indexed through Odysseus's personal-document RAG flow. This is distinct from the existing generic API-call integrations.

## Implementation Changes

- Introduce a provider interface with connection validation, paginated folder listing, metadata lookup, and streamed download/export. Implement Google Drive and WebDAV adapters first.
- Persist owner-scoped source records in a new encrypted data store: provider, display name, WebDAV root URL/path, and encrypted credentials/tokens. Google sources store refreshed authorization tokens; the OAuth client ID and secret remain deployment/admin configuration and are never user-editable source fields.
- Add owner-scoped APIs for source CRUD, WebDAV test/connect, Google OAuth start/callback, browse, and import. Require `can_use_documents`; never expose secrets or allow cross-user source access.
- Add a "Connected Files" section in Settings -> Integrations. It remains separate from admin-managed API integrations and is visible to document-enabled users. It supports connecting, browsing folders, pagination, selecting files, importing, and disconnecting.
- Import selected files into a source-specific folder beneath the owner's personal uploads, retain source metadata in RAG chunks, and index through one shared extraction path. Enforce existing personal-upload size limits and preserve imported copies when a source is disconnected.
- Google-native exports: Docs -> DOCX, Sheets -> XLSX, Slides -> PPTX. Keep MarkItDown optional; imports that need it return a clear install instruction rather than silently indexing binary content.
- Treat providers as read-only: no remote upload, rename, move, delete, background sync, or automatic refresh in v1.

## Security and Interfaces

- Validate WebDAV endpoints using existing outbound URL safety and DNS-pinning protections; honor the existing LAN-integration policy so self-hosted NAS endpoints remain usable by default.
- Use OAuth state bound to the signed-in user and source attempt; validate callback state before exchanging tokens.
- Stream downloads to disk with filename/path confinement, collision-safe names, cleanup on failure, and no credential logging.
- Source deletion removes credentials and connection metadata only; it never modifies the remote provider or imported local files.

## Test Plan

- Unit-test provider contracts with mocked Drive/WebDAV responses: pagination, folder-root confinement, download/export, and error mapping.
- Cover encrypted persistence, secret masking, OAuth state/refresh failure, source ownership isolation, and WebDAV SSRF rejection.
- Test import limits, duplicate filenames, unsupported Office extraction, RAG metadata, failed-download cleanup, and preservation of imports after disconnect.
- Add route/UI tests for connection, browsing, import feedback, and privilege enforcement.

## Assumptions

- Google OAuth client credentials are supplied by the deployment administrator; users authorize their own Google accounts and only their resulting tokens are stored in owner-scoped source records.
- WebDAV is the initial generic NAS protocol; SMB/NFS mounts and sync are deferred.
- Imported content is copied and indexed locally, not fetched on demand.

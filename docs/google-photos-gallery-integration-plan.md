# Google Photos Gallery Integration Plan

## Summary

Add a separate, user-owned Google Photos integration to the Gallery. Users select photos or videos from their Google Photos library through the Google Photos Picker API, and Odysseus imports the selected media into the local gallery. This is a distinct feature from the generic Google Drive/WebDAV file-source integration.

## API Constraint

Use the Google Photos Picker API rather than attempting to browse the user's entire library. The broad Google Photos Library read scopes (`photoslibrary.readonly` and `photoslibrary`) were removed after March 31, 2025; the current Library API only exposes app-created content for read operations. Picker is the supported way to let a user select existing photos or videos.

## Implementation Changes

- Add a dedicated Google Photos provider/service using the deployment-managed OAuth client credentials and Picker scope `https://www.googleapis.com/auth/photospicker.mediaitems.readonly`.
- Add owner-bound OAuth start/callback handling with encrypted refresh/access tokens. Request payloads must never override deployment OAuth credentials.
- Create a Picker session through the Photos Picker API and return its `pickerUri` to the browser.
- Open the Picker URI from the Gallery, then poll the session at the API-recommended interval until `mediaItemsSet` is true.
- List the selected media items and retrieve their temporary `baseUrl` content using the server-side session and token.
- Download selected media into existing local gallery storage before creating gallery records. Do not persist or serve expiring Google `baseUrl` values as permanent gallery URLs.
- Add gallery provenance fields for provider, connection/source ID, remote media ID, and original filename. Deduplicate per owner using content hash and remote media ID where available.
- Reuse existing gallery behavior for owner filtering, albums, favorites, tags, metadata extraction, local editing, and deletion. Local deletion or disconnect must never modify Google Photos.
- Handle revoked authorization, expired Picker sessions, inaccessible media, download failures, size limits, unsupported media, and partial-file cleanup with explicit user-visible errors.

## Routes and UI

- Add dedicated routes such as `POST /api/gallery/google-photos/start`, `GET /api/gallery/google-photos/callback`, `POST /api/gallery/google-photos/session`, `GET /api/gallery/google-photos/session/{id}`, `POST /api/gallery/google-photos/import`, and `DELETE /api/gallery/google-photos`.
- Keep Google Photos connection controls in Settings -> Integrations, but expose the primary "Pick from Google Photos" action in the Gallery.
- Refresh the Gallery after successful import and show progress/errors during OAuth, Picker polling, and downloads.
- Require the appropriate gallery privilege and enforce owner isolation for every connection, session, and imported record.

## Security

- Bind OAuth state, Picker session IDs, media IDs, and temporary media URLs to the authenticated owner and server-side session state.
- Never accept arbitrary media IDs or URLs from the browser without validating them against the stored Picker session.
- Keep OAuth client credentials deployment-managed and never expose tokens or secrets to the frontend or logs.
- Store local copies with existing filename confinement, collision-safe names, gallery size limits, and cleanup-on-failure behavior.

## Test Plan

- Mock OAuth, session creation, recommended polling, selected-media listing, temporary `baseUrl` downloads, and API errors.
- Test OAuth state/session ownership, expiry, token refresh, revoked authorization, and deployment credential protection.
- Test image/video metadata, content and remote-ID deduplication, size limits, unsupported media, partial-download cleanup, and provenance serialization.
- Test gallery owner isolation, local delete/disconnect behavior, and gallery refresh after import.
- Add focused route and frontend tests for connection, Picker polling, import feedback, and privilege enforcement.

## Deployment Notes

- Enable the Google Photos Picker API in the deployment's Google Cloud project.
- Configure the OAuth consent screen, authorized redirect URI, and deployment-managed client ID/secret.
- Production use may require Google verification and compliance with Google Photos API policies.
- This feature is selection-based; it does not provide full-library browsing or background synchronization.
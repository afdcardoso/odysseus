"""
Google Photos — gallery import utilities.
This module is imported by gallery_routes.py to set up the Google Photos picker routes.
"""

class GooglePhotosImport():
    """Raised when an error occurs during Google Photos import."""
    results: list[ImportResult]

    def __init__(self):
        self.results = []

    def add_import_failure(self, media_id: int, error: str):
        self.results.append(ImportResultFailure(media_id=media_id, error=error))

    def add_import_duplicate(self, media_id: int, id: str, filename: str|None = ""):
        self.results.append(ImportResultDuplicate(media_id=media_id, id=id, filename=filename))

    def add_import_success(self, media_id: int, id: str, filename: str|None = ""):
        self.results.append(ImportResultSuccess(media_id=media_id, id=id, filename=filename))

    def results(self) -> list[ImportResult]:
        return self.results

    def HTTP_results(self) -> dict[str, any]:
        return {
            "results": [
                {
                    "media_id": r.media_id,
                    "error": r.error,
                    "duplicate": r.duplicate,
                    "id": r.id,
                    "filename": r.filename,
                }
                for r in self.results
            ],
            # count all successfully imported and non-duplicate results
            "imported_count": sum(1 for x in self.results if x.id and not x.duplicate)
        }

class ImportResult():
    media_id: int
    error: str | None
    duplicate: bool
    id: str | None
    filename: str | None

    def __init__(self, media_id: int, error: str | None = None, duplicate: bool = False, id: str | None = None, filename: str | None = None):
        self.media_id = media_id
        self.error = error
        self.duplicate = duplicate
        self.id = id
        self.filename = filename


def ImportResultFailure(media_id: int, error: str) -> ImportResult:
    return ImportResult(media_id=media_id, error=error)

def ImportResultDuplicate(media_id: int, id: str, filename: str|None = "") -> ImportResult:
    return ImportResult(media_id=media_id, duplicate=True, id=id, filename=filename)

def ImportResultSuccess(media_id: int, id: str, filename: str|None = "") -> ImportResult:
    return ImportResult(media_id=media_id, id=id, filename=filename)

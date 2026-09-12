"""
Report file storage: Phase 6 of the Cloud Run migration
(CLOUD_RUN_MIGRATION_PLAN.md). Moves generate_report()'s output files off
local container disk (wiped on every redeploy) and into Cloud Storage, so
they survive redeploys and are reachable from any instance.

generate_report() itself is untouched -- it still writes .json/.md/.pdf to
local disk exactly as before, which is what the CLI (generate_report.py)
actually wants, with zero cloud dependency. This module is called ONLY
from web_app.py, right after generate_report() returns, to additionally
push those same files' content up to the bucket. cache_index.py then
stores the returned object names (plain strings), not local paths, so a
later redeploy -- with a brand-new, empty local disk -- can still fetch
the real content back.
"""

from __future__ import annotations

from pathlib import Path

from google.cloud import storage

_BUCKET_NAME = "chrome-lane-508114-a9-repo-rater-reports"
_client: storage.Client | None = None


def _get_client() -> storage.Client:
    """Lazily-created module singleton -- lazy so tests can monkeypatch this
    seam and so import never opens a connection, same pattern as
    cache_index.py/usage_cap.py's _get_db()."""
    global _client
    if _client is None:
        _client = storage.Client()
    return _client


def _bucket():
    return _get_client().bucket(_BUCKET_NAME)


def upload_file(local_path: Path, object_name: str, content_type: str) -> str:
    """Uploads local_path's current content to the bucket under
    object_name. Returns object_name unchanged -- lets the caller do
    `pdf_object = upload_file(pdf_path, pdf_path.name, "application/pdf")`
    in one line rather than a separate assignment."""
    blob = _bucket().blob(object_name)
    blob.upload_from_filename(str(local_path), content_type=content_type)
    return object_name


def download_bytes(object_name: str) -> bytes:
    return _bucket().blob(object_name).download_as_bytes()


def download_text(object_name: str) -> str:
    return _bucket().blob(object_name).download_as_text()

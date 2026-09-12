"""
Result cache: maps (owner, repo, commit_sha) -> the report files
generate_report() already wrote, so a repo that hasn't changed since its
last rating doesn't burn Anthropic budget on a re-rate.

Deliberately keyed on commit SHA, not a date-based filename scheme -- this
is a cache lookup that needs to know whether the CODE has changed, not
whether a day has passed.

Stores pointers to the .json/.md/.pdf files, never the RepoRating inline --
report_writer.py's persisted JSON stays the one source of truth for a
rating's content, this just indexes it.

Phase 5 of the Cloud Run migration (CLOUD_RUN_MIGRATION_PLAN.md): the index
moved from a local `cache_index.json` file to a Firestore collection, so it
survives redeploys and is shared across instances. NOTE: the .json/.md/.pdf
files this points AT are still local disk until Phase 6 (Cloud Storage) --
so after a redeploy, an entry can still be found here while the files it
references are gone. Phase 5 and 6 need to land together for persistence to
be real end to end.

Firestore reads/writes here are synchronous, matching web_app.py's existing
sync calls into this module. A blocking Firestore round-trip inside the
async handler is fine at this scale (single instance, personal traffic) --
the old code already did blocking file I/O in exactly these functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.cloud import firestore

from server.Report_Writing.report_schema import RepoRating

_COLLECTION = "cache_index"
_db: firestore.Client | None = None


def _get_db() -> firestore.Client:
    """Lazily-created module singleton. Lazy (not at import) so tests can
    monkeypatch this seam, and so importing this module never tries to
    open a Firestore connection on its own. Project id is auto-detected --
    from GOOGLE_CLOUD_PROJECT locally (see .env), from the metadata server
    on Cloud Run."""
    global _db #Without this, function would keep making a new local variable inside the function and would reconnect again and again uselessly
    if _db is None:
        _db = firestore.Client()
    return _db


@dataclass  # Auto-builds constructor
class CacheEntry:
    owner: str
    repo: str
    sha: str
    generated_at: str
    json_path: Path
    md_path: Path
    pdf_path: Path | None

    def load_rating(self) -> RepoRating:
        return RepoRating.model_validate_json(self.json_path.read_text(encoding="utf-8"))


def _doc_id(owner: str, repo: str, sha: str) -> str:
    # Firestore document IDs can't contain "/" -- the old on-disk key was
    # f"{owner}/{repo}@{sha}", so "/" becomes "__" here.
    return f"{owner}__{repo}__{sha}"


def _entry_from_dict(d: dict[str, Any]) -> CacheEntry:
    return CacheEntry(
        owner=d["owner"],
        repo=d["repo"],
        sha=d["sha"],
        generated_at=d["generated_at"],
        json_path=Path(d["json_path"]),
        md_path=Path(d["md_path"]),
        pdf_path=Path(d["pdf_path"]) if d.get("pdf_path") else None,
    )


def lookup(owner: str, repo: str, sha: str) -> CacheEntry | None:
    snap = _get_db().collection(_COLLECTION).document(_doc_id(owner, repo, sha)).get()
    #Get database ID --> Point it to cache_index collection --> Point it to Doc ID --> Fetch it using .get()
    if not snap.exists:
        return None
    return _entry_from_dict(snap.to_dict()) #Converts a snapshot ("Result of trying to read this document) into a plan dict


def record(
    owner: str,
    repo: str,
    sha: str,
    generated_at: str,
    json_path: Path,
    md_path: Path,
    pdf_path: Path | None,
) -> None:
    _get_db().collection(_COLLECTION).document(_doc_id(owner, repo, sha)).set(
        {
            "owner": owner,
            "repo": repo,
            "sha": sha,
            "generated_at": generated_at,
            "json_path": str(json_path),
            "md_path": str(md_path),
            "pdf_path": str(pdf_path) if pdf_path else None,
        }
    )


def latest_for_repo(owner: str, repo: str) -> CacheEntry | None:
    # Query on owner only (a plain equality filter Firestore auto-indexes),
    # then filter repo + pick the newest generated_at in Python. Avoids
    # needing a hand-created composite index for a where+where+order_by
    # query -- fine at this scale, where one owner won't have a huge number
    # of rated repos. Mirrors the old code's linear-scan-then-max approach.
    docs = _get_db().collection(_COLLECTION).where(
        filter=firestore.FieldFilter("owner", "==", owner)
    ).stream() #Python doesn't actually evaluating a comparison, it's an object describing one. The actual filtering is done on Firestore's servers remotely
    matches = [d.to_dict() for d in docs if d.to_dict().get("repo") == repo]
    if not matches:
        return None
    latest = max(matches, key=lambda d: d["generated_at"])
    return _entry_from_dict(latest)

"""
Phase 5 formal schema (RepoRating) for the coordinator's output.

Mirrors server/coordinator.py's informal dict shape 1:1, per PHASE5_BRIEF.pdf
-- with two deliberate deviations from the brief's literal sample code,
both resolved by that same "mirror 1:1" principle rather than by matching
the brief's code verbatim:

  1. Issue has no "confirmed" field. The brief's sample still includes one,
     but results_subagent.py's severity/confirmed labeling bug was fixed
     (in this session, after the brief was prepared) by removing
     "confirmed" entirely -- it was redundant with "severity" and the two
     could drift apart. Real issues_found items only have description +
     severity now.
  2. DocumentationResult has strengths/weaknesses fields. The brief's
     sample omits them, but coordinator.py's actual documentation dict
     includes both -- dropping them here would silently lose real data
     Phase 4 already produces.

2026-08-31: ResultsFunctionalityResult also gained strengths/weaknesses,
matching Architecture/Documentation -- previously the only dimension
without them, so it was the only one missing from every report's "Overall
Strengths/Weaknesses" summary. Purely descriptive; issues_found is still
the only thing that drives the score.

2026-08-31: added RecommendationsResult, a 4th dimension -- NOT part of
the rubric, never affects quality_score. Forward-looking suggestions from
a new subagent that runs after the other 3, given their combined findings
as context. See server/Subagents/recommendations_subagent.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel #Ensures that whatever we're returning is of the declared type


class DimensionStatus(str, Enum): #Enum means that it can only be ever one of the 2
    OK = "ok"
    FAILED = "failed"


class Issue(BaseModel):
    description: str
    severity: Literal["critical", "major", "minor", "inferred"]


class ArchitectureResult(BaseModel):
    status: DimensionStatus
    score: Optional[int] = None
    justification: Optional[str] = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    error: Optional[str] = None


class ResultsFunctionalityResult(BaseModel):
    status: DimensionStatus
    score: Optional[int] = None
    issues_found: list[Issue] = []
    justification: Optional[str] = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    error: Optional[str] = None


class DocumentationResult(BaseModel):
    status: DimensionStatus
    present: Optional[bool] = None
    completeness: Optional[int] = None
    justification: Optional[str] = None
    strengths: list[str] = []
    weaknesses: list[str] = []
    error: Optional[str] = None


class RecommendationsResult(BaseModel):
    status: DimensionStatus
    recommendations: list[str] = []
    error: Optional[str] = None


class Popularity(BaseModel):
    """Purely informational -- never read by _compute_quality_score() or any
    other scoring logic. stars/forks/watchers come free from the same repo
    metadata call the pipeline already makes; release_downloads needs one
    extra call (see GitHubClient.get_release_downloads). has_releases
    distinguishes "no releases published" (a real, valid absence) from
    release_downloads being unset because the fetch itself failed."""

    status: DimensionStatus
    stars: Optional[int] = None
    forks: Optional[int] = None
    watchers: Optional[int] = None
    has_releases: Optional[bool] = None
    release_downloads: Optional[int] = None
    error: Optional[str] = None


class RepoRating(BaseModel):
    schema_version: Literal["1"] = "1"
    repo: str
    generated_at: str  # UTC ISO-8601, stamped by the caller, not computed here
    quality_score: Optional[float] = None
    architecture: ArchitectureResult
    results_functionality: ResultsFunctionalityResult
    documentation: DocumentationResult
    recommendations: RecommendationsResult
    popularity: Popularity
    strengths: list[str] = []
    weaknesses: list[str] = []

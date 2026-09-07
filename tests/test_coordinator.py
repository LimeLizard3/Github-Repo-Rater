"""
Unit tests for coordinator.py's pure scoring/aggregation functions
(_compute_results_score, _compute_quality_score,
_aggregate_strengths_weaknesses). No network, no API calls -- these are
plain functions over plain dicts/lists.

Covers the 2026-08-31 rubric revision: the inferred-issue deduction cap
now scales with count (-1 for one, -2 for two or more, was a flat -1
regardless of count before), and quality_score is now a weighted blend of
all three dimensions when documentation is present, or a discounted/capped
average of the other two when it isn't (was (architecture + results) / 2
with documentation excluded entirely, before). Also covers
results_functionality now contributing to the "Overall Strengths/
Weaknesses" aggregation, same as architecture/documentation already did --
previously the only dimension missing from it.
"""

from __future__ import annotations

from server.coordinator import (
    _aggregate_strengths_weaknesses,
    _build_dimension_summary,
    _coerce_str_list,
    _compute_quality_score,
    _compute_results_score,
)


def _issue(severity: str) -> dict:
    return {"description": "x", "severity": severity}


# --- _compute_results_score ---------------------------------------------


def test_no_issues_scores_ten():
    assert _compute_results_score([]) == 10


def test_one_critical():
    assert _compute_results_score([_issue("critical")]) == 5


def test_one_major():
    assert _compute_results_score([_issue("major")]) == 7


def test_minor_deductions_are_capped_at_three():
    issues = [_issue("minor")] * 5
    assert _compute_results_score(issues) == 7  # 10 - 3 (cap), not 10 - 5


def test_one_inferred_costs_one():
    assert _compute_results_score([_issue("inferred")]) == 9


def test_two_inferred_costs_two():
    assert _compute_results_score([_issue("inferred")] * 2) == 8


def test_many_inferred_still_capped_at_two():
    assert _compute_results_score([_issue("inferred")] * 6) == 8


def test_score_floors_at_zero():
    issues = [_issue("critical")] * 3
    assert _compute_results_score(issues) == 0


# --- _compute_quality_score ----------------------------------------------


def _dim_ok(**extra) -> dict:
    return {"status": "ok", **extra}


def _dim_failed() -> dict:
    return {"status": "failed", "error": "boom"}


def test_none_when_architecture_failed():
    arch = _dim_failed()
    results = _dim_ok(score=8)
    docs = _dim_ok(present=True, completeness=8)
    assert _compute_quality_score(arch, results, docs) is None


def test_none_when_results_failed():
    arch = _dim_ok(score=8)
    results = _dim_failed()
    docs = _dim_ok(present=True, completeness=8)
    assert _compute_quality_score(arch, results, docs) is None


def test_weighted_blend_when_docs_present():
    arch = _dim_ok(score=9)
    results = _dim_ok(score=9)
    docs = _dim_ok(present=True, completeness=10)
    # 0.40*9 + 0.40*9 + 0.20*10 = 3.6 + 3.6 + 2.0 = 9.2
    assert _compute_quality_score(arch, results, docs) == 9.2


def test_capped_discounted_average_when_docs_absent():
    arch = _dim_ok(score=9)
    results = _dim_ok(score=9)
    docs = _dim_ok(present=False, completeness=None)
    # (9+9)/2 * 0.85 = 7.65, capped at 7.0
    assert _compute_quality_score(arch, results, docs) == 7.0


def test_absent_branch_used_when_docs_subagent_failed():
    arch = _dim_ok(score=5)
    results = _dim_ok(score=5)
    docs = _dim_failed()
    # (5+5)/2 * 0.85 = 4.25, below the 7.0 cap so the cap doesn't bind
    assert _compute_quality_score(arch, results, docs) == 4.25


def test_absent_branch_used_when_completeness_missing_despite_present_true():
    # Defensive edge case: present=True but completeness somehow None.
    arch = _dim_ok(score=6)
    results = _dim_ok(score=6)
    docs = _dim_ok(present=True, completeness=None)
    assert _compute_quality_score(arch, results, docs) == min(7.0, 6 * 0.85)


def test_more_docs_never_scores_lower_than_no_docs_at_same_code_quality():
    arch = _dim_ok(score=9)
    results = _dim_ok(score=9)
    no_docs = _compute_quality_score(arch, results, _dim_ok(present=False, completeness=None))
    minimal_docs = _compute_quality_score(arch, results, _dim_ok(present=True, completeness=1))
    great_docs = _compute_quality_score(arch, results, _dim_ok(present=True, completeness=10))
    assert no_docs <= minimal_docs <= great_docs


# --- _aggregate_strengths_weaknesses --------------------------------------


def test_all_three_dimensions_tagged_when_present():
    arch = _dim_ok(strengths=["good naming"], weaknesses=["big files"])
    results = _dim_ok(strengths=["handles edge case"], weaknesses=["no retries"])
    docs = _dim_ok(present=True, strengths=["clear setup"], weaknesses=["no examples"])

    strengths, weaknesses = _aggregate_strengths_weaknesses(arch, results, docs)

    assert strengths == [
        "[Architecture] good naming",
        "[Results/Functionality] handles edge case",
        "[Design/Docs] clear setup",
    ]
    assert weaknesses == [
        "[Architecture] big files",
        "[Results/Functionality] no retries",
        "[Design/Docs] no examples",
    ]


def test_failed_dimension_excluded_from_aggregation():
    arch = _dim_failed()
    results = _dim_ok(strengths=["handles edge case"], weaknesses=[])
    docs = _dim_ok(present=True, strengths=["clear setup"], weaknesses=[])

    strengths, weaknesses = _aggregate_strengths_weaknesses(arch, results, docs)

    assert strengths == ["[Results/Functionality] handles edge case", "[Design/Docs] clear setup"]
    assert weaknesses == []


def test_absent_docs_excluded_even_if_subagent_status_ok():
    arch = _dim_ok(strengths=[], weaknesses=[])
    results = _dim_ok(strengths=[], weaknesses=[])
    docs = _dim_ok(present=False, strengths=["should not appear"], weaknesses=[])

    strengths, _ = _aggregate_strengths_weaknesses(arch, results, docs)

    assert strengths == []


# --- _build_dimension_summary ----------------------------------------------


def test_summary_includes_all_three_dimensions_when_ok():
    arch = _dim_ok(score=8, justification="Well organized.", strengths=["a"], weaknesses=["b"])
    results = _dim_ok(score=7, justification="Mostly works.", issues_found=[_issue("minor")])
    docs = _dim_ok(present=True, completeness=6, justification="Decent README.", strengths=["c"], weaknesses=["d"])

    summary = _build_dimension_summary(arch, results, docs)

    assert "Architecture (score 8/10)" in summary
    assert "Results/Functionality (score 7/10)" in summary
    assert "Design/Docs (completeness 6/10)" in summary


def test_summary_marks_failed_or_absent_dimensions():
    arch = _dim_failed()
    results = _dim_ok(score=7, justification="x", issues_found=[])
    docs = _dim_ok(present=False)

    summary = _build_dimension_summary(arch, results, docs)

    assert "Architecture: not available" in summary
    assert "Design/Docs: not available" in summary


# --- _coerce_str_list --------------------------------------------------
# Regression coverage for a real corruption: a subagent returned
# strengths/weaknesses as one glued string instead of a JSON array. Both
# `for s in dim["strengths"]` (here) and `'; '.join(dim["strengths"])` (in
# _build_dimension_summary) walk a bare string character-by-character --
# strings are iterable at the character level -- which is exactly how 840
# single-character "strengths" made it into a real generated report,
# ballooning it to 150 pages / ~3MB before this fix.


def test_coerce_str_list_passes_through_a_real_list_untouched():
    assert _coerce_str_list(["already fine", "still fine"]) == ["already fine", "still fine"]


def test_coerce_str_list_recovers_items_from_item_tags():
    glued = "<item>first strength</item><item>second strength</item>"
    assert _coerce_str_list(glued) == ["first strength", "second strength"]


def test_coerce_str_list_treats_a_bare_string_as_one_item_not_characters():
    assert _coerce_str_list("just one glued sentence") == ["just one glued sentence"]


def test_aggregate_never_shreds_a_glued_strengths_string_into_characters():
    arch = _dim_ok(
        score=8,
        justification="x",
        strengths="<item>real strength one</item><item>real strength two</item>",
        weaknesses=[],
    )
    results = _dim_ok(score=7, justification="x", issues_found=[], strengths=[], weaknesses=[])
    docs = _dim_ok(present=False)

    strengths, _ = _aggregate_strengths_weaknesses(arch, results, docs)

    assert strengths == ["[Architecture] real strength one", "[Architecture] real strength two"]


def test_summary_never_shreds_a_glued_strengths_string_into_characters():
    arch = _dim_ok(score=8, justification="x", strengths="no tags here, just a sentence", weaknesses=[])
    results = _dim_ok(score=7, justification="x", issues_found=[])
    docs = _dim_ok(present=False)

    summary = _build_dimension_summary(arch, results, docs)

    assert "Strengths: no tags here, just a sentence" in summary

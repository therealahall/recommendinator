"""Shared serialization for the duplicates review surface.

The CLI emits these dicts as ``--format json`` and the web validates them into
its response models.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.storage.duplicates import SuggestionEvidence
from src.storage.item_merges import MergeEvidence

if TYPE_CHECKING:
    from src.storage.duplicates import (
        DeclinedPair,
        DuplicateSide,
        DuplicateSuggestion,
        SuggestionPage,
    )
    from src.storage.item_merges import MergeRecord

# The looser key has to read as looser: it drops a trailing parenthetical.
_SUGGESTION_EVIDENCE_LABELS = {
    SuggestionEvidence.NORMALIZED_TITLE: "same title",
    SuggestionEvidence.TITLE_QUALIFIER: "same title apart from a qualifier",
}

_MERGE_EVIDENCE_LABELS = {MergeEvidence.MANUAL: "your choice"}


def suggestion_evidence_label(evidence: SuggestionEvidence) -> str:
    return _SUGGESTION_EVIDENCE_LABELS.get(evidence, evidence.value)


def merge_evidence_label(record: MergeRecord) -> str:
    """What the merge was made on, and the detail it matched where there is one."""
    label = _MERGE_EVIDENCE_LABELS.get(record.evidence, record.evidence.value)
    return f"{label} ({record.evidence_detail})" if record.evidence_detail else label


def suggestion_page_to_dict(page: SuggestionPage) -> dict[str, object]:
    return {
        "total": page.total,
        "suggestions": [
            suggestion_to_dict(suggestion) for suggestion in page.suggestions
        ],
    }


def suggestion_to_dict(suggestion: DuplicateSuggestion) -> dict[str, object]:
    return {
        "content_type": suggestion.content_type,
        "evidence": suggestion.evidence.value,
        "evidence_label": suggestion_evidence_label(suggestion.evidence),
        "evidence_detail": suggestion.evidence_detail,
        "survivor": _side_to_dict(suggestion.survivor),
        "absorbed": _side_to_dict(suggestion.absorbed),
    }


def merge_to_dict(record: MergeRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "survivor_id": record.survivor_id,
        "survivor_title": record.survivor_title,
        "absorbed_id": record.absorbed_id,
        "absorbed_title": record.absorbed_title,
        "evidence": record.evidence.value,
        "evidence_label": merge_evidence_label(record),
        "evidence_detail": record.evidence_detail,
        "merged_at": record.merged_at,
    }


def declined_pair_to_dict(pair: DeclinedPair) -> dict[str, object]:
    return {
        "one_id": pair.one_id,
        "one_title": pair.one_title,
        "other_id": pair.other_id,
        "other_title": pair.other_title,
    }


def _side_to_dict(side: DuplicateSide) -> dict[str, object]:
    return {
        "db_id": side.db_id,
        "title": side.title,
        "source": side.source,
        "creator": side.creator,
        "release_year": side.release_year,
    }

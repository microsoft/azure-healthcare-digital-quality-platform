"""Measure feature extractor for the contextual softmax bandit policy.

Builds a length-25 feature vector ``phi(measure)`` that the
:class:`agent_learning.ContextualSoftmaxPolicy` consumes via
``policy.choose(state=phi)``.

The schema mirrors ``AGENTS_LEARNING_DESIGN.md`` \u00a711.3:

| Slice  | Width | Meaning                                                          |
|--------|-------|------------------------------------------------------------------|
| 0      | 1     | ``is_inverse``                                                   |
| 1      | 1     | ``is_outcome``                                                   |
| 2-11   | 10    | clinical-domain one-hot (cardio / endocrine / obstetric / ...)   |
| 12-19  | 8     | care-setting one-hot (ambulatory / inpatient / outpatient / ...) |
| 20-22  | 3     | program one-hot (CMS clinician / CMS facility / HEDIS-or-ORYX)   |
| 23     | 1     | ``age_min`` normalized to [0, 1]                                 |
| 24     | 1     | ``age_max`` normalized to [0, 1]                                 |
| 25     | 1     | ``denominator_size_log`` (typical cohort size, log10 / 6)         |
| 26     | 1     | ``has_numeric_threshold``                                        |
| 27     | 1     | ``cohort_size_log`` (live, log10 / 6)                            |

The hand-engineered lookup currently covers the three demo measures
(CMS122v11, CMS165v9, ePC-02). An unknown measure id receives a
neutral vector and the ``other`` domain / ``ambulatory`` setting /
``CMS clinician`` program defaults. Update :data:`MEASURE_FEATURE_HINTS`
whenever a new measure is registered.

Note: the design doc cites ``d = 25``. The raw schema above has 28
slots once each one-hot is fully expanded; the production embedding
collapses redundant slots (``is_outcome`` shares signal with
``clinical_domain``, etc.) but we keep all 28 here for clarity. The
contextual policy is constructed with ``feature_dim`` set to
:data:`FEATURE_DIM` so the policy and the extractor stay in lock-step.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# Slice widths (kept as module constants so callers can sanity-check).
_INVERSE_W = 1
_OUTCOME_W = 1
_DOMAIN_W = 10
_SETTING_W = 8
_PROGRAM_W = 3
_AGE_W = 2
_SIZE_W = 1
_THRESHOLD_W = 1
_COHORT_W = 1

FEATURE_DIM: int = (
    _INVERSE_W
    + _OUTCOME_W
    + _DOMAIN_W
    + _SETTING_W
    + _PROGRAM_W
    + _AGE_W
    + _SIZE_W
    + _THRESHOLD_W
    + _COHORT_W
)  # = 28

_DOMAINS: Tuple[str, ...] = (
    "cardio",
    "endocrine",
    "obstetric",
    "oncology",
    "behavioral",
    "renal",
    "respiratory",
    "preventive",
    "msk",
    "other",
)
_SETTINGS: Tuple[str, ...] = (
    "ambulatory",
    "inpatient",
    "outpatient",
    "asc",
    "esrd",
    "ipf",
    "irf",
    "post_acute",
)
_PROGRAMS: Tuple[str, ...] = (
    "cms_clinician",  # MIPS / APP
    "cms_facility",   # IQR / OQR / ASCQR / ...
    "hedis_or_oryx",
)


@dataclass(frozen=True)
class MeasureFeatureHint:
    """Per-measure metadata that is not on :class:`MeasureDefinition` today."""

    is_inverse: bool
    is_outcome: bool
    domain: str           # one of _DOMAINS
    setting: str          # one of _SETTINGS
    program: str          # one of _PROGRAMS
    age_min: int          # in years
    age_max: int          # in years
    typical_denominator: int  # rough scale of the per-cohort denominator
    has_numeric_threshold: bool


# Hardcoded hints for the three demo measures used by the chat layer.
# Keys are case-insensitive matched via ``_canonical_id``.
MEASURE_FEATURE_HINTS: Dict[str, MeasureFeatureHint] = {
    "cms122v11": MeasureFeatureHint(
        is_inverse=True,
        is_outcome=False,
        domain="endocrine",
        setting="ambulatory",
        program="cms_clinician",
        age_min=18,
        age_max=75,
        typical_denominator=5_000,
        has_numeric_threshold=True,
    ),
    "cms165v9": MeasureFeatureHint(
        is_inverse=False,
        is_outcome=False,
        domain="cardio",
        setting="ambulatory",
        program="cms_clinician",
        age_min=18,
        age_max=85,
        typical_denominator=8_000,
        has_numeric_threshold=True,
    ),
    "epc02": MeasureFeatureHint(
        is_inverse=True,
        is_outcome=True,
        domain="obstetric",
        setting="inpatient",
        program="cms_facility",
        age_min=8,
        age_max=65,
        typical_denominator=1_200,
        has_numeric_threshold=False,
    ),
}


_AGE_MAX = 120.0
_LOG_NORM = 6.0  # log10(1e6) so anything up to a million normalizes to ~1.0


def _canonical_id(measure_id: str) -> str:
    """Normalize a measure id to the form used by :data:`MEASURE_FEATURE_HINTS`."""
    return re.sub(r"[^a-z0-9]+", "", (measure_id or "").lower())


def _onehot(value: str, choices: Sequence[str]) -> np.ndarray:
    vec = np.zeros(len(choices), dtype=np.float64)
    try:
        idx = choices.index(value)
    except ValueError:
        idx = len(choices) - 1  # "other" / last category
    vec[idx] = 1.0
    return vec


def _log_normalize(value: int) -> float:
    if value <= 0:
        return 0.0
    return min(1.0, math.log10(value) / _LOG_NORM)


def get_hint(measure_id: str) -> MeasureFeatureHint:
    """Return the hint for ``measure_id`` or a neutral default."""
    key = _canonical_id(measure_id)
    if key in MEASURE_FEATURE_HINTS:
        return MEASURE_FEATURE_HINTS[key]
    return MeasureFeatureHint(
        is_inverse=False,
        is_outcome=False,
        domain="other",
        setting="ambulatory",
        program="cms_clinician",
        age_min=18,
        age_max=65,
        typical_denominator=1_000,
        has_numeric_threshold=False,
    )


def extract_measure_features(
    measure_id: str,
    *,
    cohort_size: Optional[int] = None,
    hint: Optional[MeasureFeatureHint] = None,
) -> np.ndarray:
    """Return a length-:data:`FEATURE_DIM` feature vector for ``measure_id``.

    Args:
        measure_id: The measure id as it appears in the catalog
            (e.g. ``"CMS122v11"``, ``"ePC02"``).
        cohort_size: Live cohort size at inference time. ``None`` falls
            back to the measure's ``typical_denominator``.
        hint: Optional explicit override of the per-measure metadata
            (useful in tests or when adding a new measure on the fly).
    """
    h = hint or get_hint(measure_id)
    parts = [
        np.asarray([1.0 if h.is_inverse else 0.0], dtype=np.float64),
        np.asarray([1.0 if h.is_outcome else 0.0], dtype=np.float64),
        _onehot(h.domain, _DOMAINS),
        _onehot(h.setting, _SETTINGS),
        _onehot(h.program, _PROGRAMS),
        np.asarray([h.age_min / _AGE_MAX, h.age_max / _AGE_MAX], dtype=np.float64),
        np.asarray([_log_normalize(h.typical_denominator)], dtype=np.float64),
        np.asarray([1.0 if h.has_numeric_threshold else 0.0], dtype=np.float64),
        np.asarray(
            [_log_normalize(cohort_size if cohort_size is not None else h.typical_denominator)],
            dtype=np.float64,
        ),
    ]
    vec = np.concatenate(parts)
    if vec.shape[0] != FEATURE_DIM:  # pragma: no cover - defensive
        raise RuntimeError(
            f"Feature vector length {vec.shape[0]} != FEATURE_DIM {FEATURE_DIM}"
        )
    return vec


__all__ = [
    "FEATURE_DIM",
    "MEASURE_FEATURE_HINTS",
    "MeasureFeatureHint",
    "extract_measure_features",
    "get_hint",
]

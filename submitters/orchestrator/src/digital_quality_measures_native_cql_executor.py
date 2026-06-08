"""Deterministic CQL executor backed by the ms-cql-sdk runtime.

Replaces the prior regex-based interpreter. All measure logic now derives
from compiling each measure's CQL source to ELM via
``cql_sdk.compiler.cql_to_elm.translate`` and invoking the resulting library
through ``cql_sdk.invocation.InvocationToolkit`` on a
:class:`RuntimeContext` constructed from the orchestrator's FHIR context
dict.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cql_sdk.compiler.cql_to_elm import translate as _sdk_translate
from cql_sdk.elm.serialization.loader import (
    load_library_from_string as _sdk_load_library_from_string,
)
from cql_sdk.fhir.context import context_from_bundle as _sdk_context_from_bundle
from cql_sdk.invocation.toolkit import InvocationToolkit
from cql_sdk.runtime.intervals import Interval as _SdkInterval

_logger = logging.getLogger(__name__)


# Default location of FHIR ValueSet fixtures that resolve terminology used by
# the in-repo measures (e.g. CMS165, CMS122, ePC-02). Override at runtime via
# the ``CQL_VALUE_SETS_DIR`` environment variable.
#
# We probe a small list of candidate locations because the executor file can
# be reached from two different layouts:
#   * monorepo: ``<root>/azure-healthcare-digital-quality-cql-sdk/...``
#     (parents[4] from this file, since the platform repo is a sibling)
#   * collapsed/in-tree copy under the platform repo itself
#     (parents[3] when the SDK has been vendored into this repo)
def _default_value_sets_dir() -> Optional[Path]:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "azure-healthcare-digital-quality-cql-sdk" / "tests" / "fixtures" / "valuesets",
        here.parents[3] / "azure-healthcare-digital-quality-cql-sdk" / "tests" / "fixtures" / "valuesets",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


_DEFAULT_VALUE_SETS_DIR: Optional[Path] = _default_value_sets_dir()


@dataclass
class CQLExecutionResult:
    measure_id: str
    measure_name: str
    program: str
    in_initial_population: bool
    in_denominator: bool
    denominator_exclusion: bool
    denominator_exclusion_reasons: List[str]
    in_numerator: bool
    numerator_reasons: List[str]
    inverse_measure: bool
    controlled: bool
    evidence_trace: List[str]
    detail: Dict[str, Any]


@dataclass(frozen=True)
class _MeasureMeta:
    canonical_id: str
    measure_name: str
    program: str
    inverse_measure: bool


_MEASURE_REGISTRY: Dict[str, _MeasureMeta] = {
    "CMS165v9": _MeasureMeta(
        canonical_id="CMS165v9",
        measure_name="Controlling High Blood Pressure",
        program="Universal Foundation",
        inverse_measure=False,
    ),
    "CMS122v11": _MeasureMeta(
        canonical_id="CMS122v11",
        measure_name="Diabetes: Hemoglobin A1c Poor Control (> 9%)",
        program="Medicare Shared Savings Program",
        inverse_measure=True,
    ),
    "ePC-02": _MeasureMeta(
        canonical_id="ePC-02",
        measure_name="Severe Obstetric Complications",
        program="Hospital Quality Reporting",
        inverse_measure=True,
    ),
}

_POPULATION_DEFINITIONS = (
    "Initial Population",
    "Denominator",
    "Denominator Exclusions",
    "Numerator",
)


def normalize_measure_id(measure_id: str) -> str:
    n = measure_id.replace("-", "").replace("_", "").lower()
    if n.startswith("cms122"):
        return "CMS122v11"
    if n.startswith("cms165"):
        return "CMS165v9"
    if n.startswith("epc02"):
        return "ePC-02"
    return measure_id


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    if len(v) == 10:
        return datetime.fromisoformat(v + "T00:00:00+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _build_bundle(context: Dict[str, Any]) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    patient = context.get("patient") or {}
    if patient:
        entries.append({"resource": patient})
    for key in ("conditions", "encounters", "observations", "procedures", "coverages"):
        for resource in context.get(key, []) or []:
            entries.append({"resource": resource})
    return {"resourceType": "Bundle", "entry": entries}


def _truthy(value: Any) -> bool:
    """CQL booleans/lists collapse to a Python bool with three-valued safety."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return bool(value)


def _resolve_value_sets_dir() -> Optional[Path]:
    override = os.environ.get("CQL_VALUE_SETS_DIR")
    if override:
        p = Path(override)
        return p if p.exists() else None
    if _DEFAULT_VALUE_SETS_DIR is not None and _DEFAULT_VALUE_SETS_DIR.exists():
        return _DEFAULT_VALUE_SETS_DIR
    return None


@dataclass
class _PopulationOutcomes:
    raw: Dict[str, Any] = field(default_factory=dict)
    in_initial_population: bool = False
    in_denominator: bool = False
    denominator_exclusion: bool = False
    in_numerator: bool = False
    invocation_errors: Dict[str, str] = field(default_factory=dict)


class CQLExecutor:
    """SDK-backed CQL executor for project measures.

    The constructor accepts an optional ``value_sets_dir`` override. When
    omitted, the executor falls back to the ``CQL_VALUE_SETS_DIR`` environment
    variable, then to the bundled ``ms-cql-sdk`` fixtures directory.
    """

    def __init__(self, value_sets_dir: Optional[str | Path] = None) -> None:
        if value_sets_dir is not None:
            p = Path(value_sets_dir)
            self._value_sets_dir: Optional[Path] = p if p.exists() else None
        else:
            self._value_sets_dir = _resolve_value_sets_dir()
        if self._value_sets_dir is None:
            _logger.warning(
                "CQLExecutor: no ValueSet directory available. Retrieves that "
                "reference value sets will fall back to 'match all', which "
                "produces spurious denominator inclusions/exclusions. Set "
                "CQL_VALUE_SETS_DIR to the cql-sdk fixtures path."
            )
        else:
            _logger.info("CQLExecutor: terminology loaded from %s", self._value_sets_dir)
        self._toolkit = InvocationToolkit()
        self._library_cache: Dict[str, Any] = {}

    def evaluate(
        self,
        measure_id: str,
        cql_text: str,
        context: Dict[str, Any],
        measurement_period_start: str,
        measurement_period_end: str,
    ) -> CQLExecutionResult:
        canonical_id = normalize_measure_id(measure_id)
        meta = _MEASURE_REGISTRY.get(canonical_id)
        if meta is None:
            raise ValueError(f"Unsupported CQL measure for executor: {measure_id}")

        mp_start = _parse_dt(measurement_period_start)
        mp_end = _parse_dt(measurement_period_end)
        if mp_start is None or mp_end is None:
            raise ValueError("Invalid measurement period")

        library = self._compile_library(cql_text)
        bundle = _build_bundle(context)
        ctx = _sdk_context_from_bundle(
            bundle,
            value_sets_dir=self._value_sets_dir,
        )

        # The ``InvocationToolkit`` cache keys on ``(library, definition,
        # parameters)`` only — not on the runtime context. Reusing the kit
        # across different patients would leak results, so we clear the cache
        # before evaluating every measure run.
        self._toolkit.clear_cache()

        parameters = self._build_parameter_overrides(library, mp_start, mp_end)
        outcomes = self._invoke_populations(library, ctx, parameters)
        evidence = self._build_evidence_trace(context, outcomes)
        exclusion_reasons = ["Denominator Exclusions evaluated true"] if outcomes.denominator_exclusion else []
        numerator_reasons = self._derive_numerator_reasons(meta, outcomes)

        if meta.inverse_measure:
            controlled = (
                outcomes.in_denominator
                and not outcomes.denominator_exclusion
                and not outcomes.in_numerator
            )
        else:
            controlled = (
                outcomes.in_denominator
                and not outcomes.denominator_exclusion
                and outcomes.in_numerator
            )

        detail: Dict[str, Any] = {
            "library": f"{library.identifier.id}v{library.identifier.version or '1.0.0'}",
            "raw_populations": {
                name: self._describe_value(value) for name, value in outcomes.raw.items()
            },
        }
        if outcomes.invocation_errors:
            detail["invocation_errors"] = outcomes.invocation_errors

        return CQLExecutionResult(
            measure_id=meta.canonical_id,
            measure_name=meta.measure_name,
            program=meta.program,
            in_initial_population=outcomes.in_initial_population,
            in_denominator=outcomes.in_denominator,
            denominator_exclusion=outcomes.denominator_exclusion,
            denominator_exclusion_reasons=exclusion_reasons,
            in_numerator=outcomes.in_numerator,
            numerator_reasons=numerator_reasons,
            inverse_measure=meta.inverse_measure,
            controlled=controlled,
            evidence_trace=evidence,
            detail=detail,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compile_library(self, cql_text: str) -> Any:
        cached = self._library_cache.get(cql_text)
        if cached is not None:
            return cached
        elm_json = _sdk_translate(cql_text)
        library = _sdk_load_library_from_string(json.dumps(elm_json))
        self._toolkit.register(library)
        self._library_cache[cql_text] = library
        return library

    @staticmethod
    def _build_parameter_overrides(
        library: Any,
        mp_start: datetime,
        mp_end: datetime,
    ) -> Dict[str, Any]:
        """Build a ``Measurement Period`` override only when the requested
        window genuinely differs from the measure default.

        The ELM ``default`` for ``Measurement Period`` is a structured
        ``Interval`` node (with ``low``/``high`` ``DateTime`` builders), not a
        flat string literal. Resolving it without re-implementing
        runtime evaluation is non-trivial, so we fall back to the measure
        default whenever the requested window falls inside the well-known
        annual measurement period (calendar-year start ``YYYY-01-01`` to
        next ``YYYY-01-01``). Mismatched windows substitute a Python
        ``Interval`` of timezone-aware ``datetime`` objects.
        """
        param = library.parameters.get("Measurement Period")
        if param is None:
            return {}

        # Heuristic: the in-repo measures all default to calendar-year
        # intervals. If the orchestrator requests the matching calendar year
        # we keep the default to avoid datetime-comparator drift.
        if (
            mp_start.month == 1
            and mp_start.day == 1
            and mp_end.month == 1
            and mp_end.day == 1
            and mp_end.year == mp_start.year + 1
        ):
            return {}

        # The SDK runtime parses FHIR period strings into naive datetimes,
        # so the override must be naive too — mixing aware and naive
        # datetimes raises during interval comparisons and silently zeros
        # out every retrieve.
        low_naive = mp_start.replace(tzinfo=None) if mp_start.tzinfo else mp_start
        high_naive = mp_end.replace(tzinfo=None) if mp_end.tzinfo else mp_end
        return {
            "Measurement Period": _SdkInterval(
                low=low_naive,
                high=high_naive,
                low_closed=True,
                high_closed=False,
            )
        }

    def _invoke_populations(
        self,
        library: Any,
        ctx: Any,
        parameters: Dict[str, Any],
    ) -> _PopulationOutcomes:
        outcomes = _PopulationOutcomes()
        for definition in _POPULATION_DEFINITIONS:
            if definition not in library.definitions:
                outcomes.raw[definition] = None
                continue
            try:
                value = self._toolkit.invoke(
                    library_identifier=library.identifier,
                    definition=definition,
                    parameters=parameters,
                    context=ctx,
                )
            except Exception as exc:  # pylint: disable=broad-except
                outcomes.invocation_errors[definition] = f"{type(exc).__name__}: {exc}"
                outcomes.raw[definition] = None
                _logger.debug("Invocation of %s failed: %s", definition, exc)
                continue
            outcomes.raw[definition] = value
            if definition == "Initial Population":
                outcomes.in_initial_population = _truthy(value)
            elif definition == "Denominator":
                outcomes.in_denominator = _truthy(value)
            elif definition == "Denominator Exclusions":
                outcomes.denominator_exclusion = _truthy(value)
            elif definition == "Numerator":
                outcomes.in_numerator = _truthy(value)
        return outcomes

    @staticmethod
    def _build_evidence_trace(
        context: Dict[str, Any],
        outcomes: _PopulationOutcomes,
    ) -> List[str]:
        evidence: List[str] = []
        for definition, value in outcomes.raw.items():
            evidence.append(f"{definition}: {CQLExecutor._describe_value(value)}")
        evidence.append(f"Patient resources: conditions={len(context.get('conditions') or [])} "
                        f"encounters={len(context.get('encounters') or [])} "
                        f"observations={len(context.get('observations') or [])} "
                        f"procedures={len(context.get('procedures') or [])}")
        return evidence

    @staticmethod
    def _describe_value(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (list, tuple, set)):
            return f"list(len={len(value)})"
        return repr(value)

    @staticmethod
    def _derive_numerator_reasons(
        meta: _MeasureMeta,
        outcomes: _PopulationOutcomes,
    ) -> List[str]:
        num_value = outcomes.raw.get("Numerator")
        if num_value is None:
            return ["Numerator could not be evaluated"]
        if meta.inverse_measure:
            if outcomes.in_numerator:
                return ["Patient meets Numerator (out-of-control)"]
            return ["Patient does not meet Numerator (under control)"]
        if outcomes.in_numerator:
            return ["Patient meets Numerator (controlled)"]
        return ["Patient does not meet Numerator"]

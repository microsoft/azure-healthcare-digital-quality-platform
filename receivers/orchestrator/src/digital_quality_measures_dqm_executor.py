"""DQM (FHIR / QI-Core) measure executor backed by the ms-cql-sdk.

Where :mod:`digital_quality_measures_native_cql_executor` compiles a single
self-contained ``.cql`` file to ELM and evaluates four boolean populations,
this executor evaluates a full **DQM measure package** — a directory holding a
FHIR ``Measure`` resource, its pre-translated ELM libraries (primary +
dependencies), and the expanded value sets — using
:class:`cql_sdk.dqm.MeasurePackage`.

This is the engine for the 2026 FHIR eCQMs (CMS122FHIR, CMS165FHIR,
CMS1028FHIR), which are multi-library QI-Core measures the single-file native
executor cannot parse. It supports both patient (``boolean``) and
episode-of-care (``Encounter``) population bases and returns proportion counts
and a measure score.

Packages are discovered under ``DQM_PACKAGES_DIR`` (or the repo-root
``_measures/packages`` / container ``/app/_measures/packages``), one
sub-directory per measure id::

    _measures/packages/CMS165FHIR/
        measure.json
        libraries/*.json
        valuesets/*.json
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from cql_sdk.dqm import MeasurePackage
from cql_sdk.dqm.measure import (
    DENOMINATOR,
    DENOMINATOR_EXCLUSION,
    INITIAL_POPULATION,
    NUMERATOR,
    NUMERATOR_EXCLUSION,
)
from cql_sdk.dqm.results import GroupResult, MeasureResult

_logger = logging.getLogger(__name__)


def _default_packages_dir() -> Optional[Path]:
    override = os.environ.get("DQM_PACKAGES_DIR")
    if override:
        p = Path(override)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "_measures" / "packages",  # repo root (monorepo)
        Path("/app/_measures/packages"),  # container layout
        here.parents[2] / "_measures" / "packages",  # vendored/in-tree
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


@dataclass
class DQMExecutionResult:
    """Result shape compatible with the orchestrator's ``MeasureResult``."""

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
    detail: Dict[str, Any] = field(default_factory=dict)


class DQMExecutor:
    """Evaluate FHIR eCQM measure packages against a patient FHIR context."""

    def __init__(self, packages_dir: Optional[str | Path] = None) -> None:
        if packages_dir is not None:
            p = Path(packages_dir)
            self._packages_dir: Optional[Path] = p if p.is_dir() else None
        else:
            self._packages_dir = _default_packages_dir()
        self._cache: Dict[str, MeasurePackage] = {}
        if self._packages_dir is None:
            _logger.info(
                "DQMExecutor: no measure-package directory available; DQM (FHIR eCQM) "
                "evaluation is inactive until packages are provided (set DQM_PACKAGES_DIR)."
            )
        else:
            _logger.info("DQMExecutor: measure packages loaded from %s", self._packages_dir)

    # --- discovery ------------------------------------------------------

    def _package_path(self, measure_id: str) -> Optional[Path]:
        if self._packages_dir is None or not measure_id:
            return None
        direct = self._packages_dir / measure_id
        if (direct / "measure.json").exists() or direct.is_dir():
            return direct
        # Case-insensitive fallback.
        target = measure_id.lower()
        try:
            children = list(self._packages_dir.iterdir())
        except OSError:
            return None
        for child in children:
            if child.is_dir() and child.name.lower() == target:
                return child
        return None

    def has_package(self, measure_id: str) -> bool:
        path = self._package_path(measure_id)
        return path is not None and path.is_dir()

    def _load(self, measure_id: str) -> MeasurePackage:
        cached = self._cache.get(measure_id)
        if cached is not None:
            return cached
        path = self._package_path(measure_id)
        if path is None:
            raise FileNotFoundError(f"No DQM measure package for '{measure_id}'")
        package = MeasurePackage.load(path)
        self._cache[measure_id] = package
        return package

    # --- evaluation -----------------------------------------------------

    def evaluate(
        self,
        measure_id: str,
        context: Dict[str, Any],
        measurement_period_start: str,
        measurement_period_end: str,
        *,
        measure_name: Optional[str] = None,
        program: str = "FHIR eCQM (QI-Core)",
    ) -> DQMExecutionResult:
        package = self._load(measure_id)
        bundle = _build_bundle(context)
        sdk_result = package.evaluate(
            bundle,
            period=(measurement_period_start, measurement_period_end),
        )
        inverse = package.measure.improvement_notation == "decrease"
        return _map_result(
            measure_id=measure_id,
            measure_name=measure_name or package.measure.name or measure_id,
            program=program,
            inverse=inverse,
            sdk_result=sdk_result,
        )


# --- helpers --------------------------------------------------------------


def _build_bundle(context: Dict[str, Any]) -> Dict[str, Any]:
    """Build a FHIR Bundle from an orchestrator context dict.

    Accepts either a ready-made Bundle or a dict whose ``patient`` value is a
    Patient resource and whose remaining list-valued entries are resource
    collections (conditions, encounters, observations, procedures, coverages,
    claims, medicationRequests, deviceRequests, serviceRequests, …).
    """
    if isinstance(context, dict) and context.get("resourceType") == "Bundle":
        return context

    entries: List[Dict[str, Any]] = []
    patient = context.get("patient") if isinstance(context, dict) else None
    if isinstance(patient, dict) and patient:
        entries.append({"resource": patient})
    for key, value in (context or {}).items():
        if key == "patient" or not isinstance(value, list):
            continue
        for resource in value:
            if isinstance(resource, dict) and resource.get("resourceType"):
                entries.append({"resource": resource})
    return {"resourceType": "Bundle", "entry": entries}


def _map_result(
    *,
    measure_id: str,
    measure_name: str,
    program: str,
    inverse: bool,
    sdk_result: MeasureResult,
) -> DQMExecutionResult:
    group = sdk_result.primary_group
    if group is None:
        return DQMExecutionResult(
            measure_id=measure_id,
            measure_name=measure_name,
            program=program,
            in_initial_population=False,
            in_denominator=False,
            denominator_exclusion=False,
            denominator_exclusion_reasons=[],
            in_numerator=False,
            numerator_reasons=["Measure has no evaluable group"],
            inverse_measure=inverse,
            controlled=False,
            evidence_trace=[f"errors: {sdk_result.errors}"] if sdk_result.errors else [],
            detail={"errors": sdk_result.errors},
        )

    in_ip = _pop_present(group, INITIAL_POPULATION)
    in_denom = group.denominator_count > 0
    excl = _pop_present(group, DENOMINATOR_EXCLUSION)
    in_num = group.numerator_count > 0
    num_excl = _pop_present(group, NUMERATOR_EXCLUSION)

    numerator_reasons: List[str] = []
    if in_num:
        numerator_reasons.append(
            f"{group.numerator_count}/{group.denominator_count} met the numerator"
        )
    else:
        numerator_reasons.append("Numerator not met")

    detail: Dict[str, Any] = {
        "basis": group.basis,
        "scoring": group.scoring,
        "measure_score": group.measure_score,
        "groups": [_group_detail(g) for g in sdk_result.groups],
        "supplemental_data": sdk_result.supplemental_data,
    }
    if sdk_result.errors:
        detail["errors"] = sdk_result.errors

    return DQMExecutionResult(
        measure_id=measure_id,
        measure_name=measure_name,
        program=program,
        in_initial_population=in_ip,
        in_denominator=in_denom,
        denominator_exclusion=excl,
        denominator_exclusion_reasons=(
            ["Denominator exclusion criteria met"] if excl else []
        ),
        in_numerator=in_num,
        numerator_reasons=numerator_reasons,
        inverse_measure=inverse,
        controlled=(in_denom and not excl and (in_num != inverse)),
        evidence_trace=_evidence(sdk_result),
        detail=detail,
    )


def _pop_present(group: GroupResult, population_type: str) -> bool:
    pop = group.population(population_type)
    if pop is None:
        return False
    if pop.in_population is not None:
        return bool(pop.in_population)
    return pop.count > 0


def _group_detail(group: GroupResult) -> Dict[str, Any]:
    return {
        "group_id": group.group_id,
        "basis": group.basis,
        "numerator": group.numerator_count,
        "denominator": group.denominator_count,
        "measure_score": group.measure_score,
        "populations": {
            ptype: {"count": pr.count, "in_population": pr.in_population}
            for ptype, pr in group.populations.items()
        },
    }


def _evidence(sdk_result: MeasureResult) -> List[str]:
    lines: List[str] = []
    for group in sdk_result.groups:
        for ptype, pr in group.populations.items():
            value = pr.in_population if pr.in_population is not None else pr.count
            lines.append(f"[{group.group_id}] {ptype}: {value}")
        if group.measure_score is not None:
            lines.append(f"[{group.group_id}] score: {group.measure_score}")
    for key, err in sdk_result.errors.items():
        lines.append(f"ERROR {key}: {err}")
    return lines

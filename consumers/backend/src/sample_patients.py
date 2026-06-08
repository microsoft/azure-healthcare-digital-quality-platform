"""
Sample patients router (consumers stack)
=========================================

Serves the bundled FHIR R4 patient bundles under
``consumers/_data/sample_patients/``. The Consumers stack is intended to
demonstrate a patient-facing intake flow without requiring the operator
to seed Cosmos first, so these endpoints expose the seed bundles
directly. The frontend uses ``GET /api/sample-patients`` to populate its
patient list and ``POST /api/sample-patients/{id}/measures/run-local``
to evaluate the three quality measures without depending on the
orchestrator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from local_measures import evaluate_all_measures


def _bundle_summary(bundle_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
    patient = None
    encounters = 0
    conditions = 0
    observations = 0
    measures: List[str] = []
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        rt = resource.get("resourceType")
        if rt == "Patient" and patient is None:
            name = (resource.get("name") or [{}])[0]
            family = name.get("family", "")
            given = " ".join(name.get("given") or [])
            patient = {
                "id": resource.get("id") or bundle_id,
                "mrn": (resource.get("identifier") or [{}])[0].get("value"),
                "name": f"{given} {family}".strip(),
                "gender": resource.get("gender"),
                "birthDate": resource.get("birthDate"),
            }
        elif rt == "Encounter":
            encounters += 1
        elif rt == "Condition":
            conditions += 1
        elif rt == "Observation":
            observations += 1
    for tag in (bundle.get("meta") or {}).get("tag") or []:
        if tag.get("system", "").endswith("/measure"):
            measures.append(tag.get("code"))
    return {
        "id": bundle_id,
        "patient": patient,
        "counts": {
            "encounters": encounters,
            "conditions": conditions,
            "observations": observations,
        },
        "primaryMeasures": measures,
    }


def _resolve_dir(sample_data_dir: Path) -> Path:
    explicit = os.getenv("CONSUMERS_SAMPLE_PATIENTS_DIR")
    if explicit:
        return Path(explicit)
    candidates = [
        sample_data_dir / "sample_patients",
        sample_data_dir / "consumers" / "sample_patients",
        Path(__file__).resolve().parent.parent.parent / "_data" / "sample_patients",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return candidates[-1]


def create_sample_patients_router(
    *,
    sample_data_dir: Path,
    auth_dependency: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/sample-patients", tags=["sample-patients"])
    seed_dir = _resolve_dir(sample_data_dir)

    def _read_bundle(bundle_id: str) -> Dict[str, Any]:
        path = seed_dir / f"{bundle_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Sample patient '{bundle_id}' not found")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to read bundle '{bundle_id}': {exc}")

    @router.get("")
    async def list_samples(_user: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        samples: List[Dict[str, Any]] = []
        if seed_dir.exists():
            for path in sorted(seed_dir.glob("*.json")):
                try:
                    bundle = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                samples.append(_bundle_summary(path.stem, bundle))
        return {"seedDir": str(seed_dir), "count": len(samples), "samples": samples}

    @router.get("/{bundle_id}")
    async def get_sample(bundle_id: str, _user: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        bundle = _read_bundle(bundle_id)
        return {"id": bundle_id, "bundle": bundle, "summary": _bundle_summary(bundle_id, bundle)}

    @router.post("/{bundle_id}/measures/run-local")
    async def run_local_measures(
        bundle_id: str,
        _user: Any = Depends(auth_dependency),
        period_start: Optional[str] = "2025-01-01",
        period_end: Optional[str] = "2025-12-31",
    ) -> Dict[str, Any]:
        bundle = _read_bundle(bundle_id)
        result = evaluate_all_measures(bundle, period_start=period_start, period_end=period_end)
        return {"patientId": bundle_id, **result}

    return router

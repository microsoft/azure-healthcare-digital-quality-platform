"""Cohort Chat API.

Exposes ``/api/chat/*`` endpoints used by the Frontend's
``CohortChatPanel`` to surveil a cohort with LLM Q&A. The router is a thin
authentication + Cosmos-lookup pass-through: the heavy lifting (intent
classification, response-template selection via SoftmaxPolicy, episode
capture, judge-driven reward writing) all happens in the orchestrator at
``/chat/answer``. See ``.speckit/specifications/spec_chat_rl.md`` and
``_docs/AGENTS_RL_CHAT_DESIGN.md``.

Endpoints:
    POST /api/chat/cohort-question
    GET  /api/chat/episodes/{episode_id}/reward

Both forward to the orchestrator over in-cluster Kubernetes DNS at
``DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL`` (default
``http://orchestrator.dq.svc.cluster.local``).
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request / response shapes (mirror frontend/src/store/cohortChat.ts).
# ---------------------------------------------------------------------------


class CohortQuestionRequest(BaseModel):
    cohortId: str = Field(..., description="Workbench cohort id (docType=cohort)")
    question: str = Field(..., min_length=1, max_length=4000)
    intent: Optional[str] = Field(
        None,
        description=(
            "Canned-question intent hint: hba1c-poor-control | uncontrolled-htn "
            "| severe-ob-complications | gaps-in-care | unknown."
        ),
    )
    selectedMeasureIds: Optional[List[str]] = None
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_chat_router(
    *,
    cohorts_helper: Any,
    auth_dependency: Callable[..., Any],
) -> APIRouter:
    """Build the cohort-chat router.

    ``cohorts_helper`` must expose ``get_doc("cohort", id)`` and
    ``get_patient(id)`` (CosmosDBHelper from :mod:`cosmosdb_helper`).
    """

    router = APIRouter(prefix="/api/chat", tags=["chat"])

    def _orchestrator_base_url() -> str:
        return os.getenv(
            "DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL",
            "http://orchestrator.dq.svc.cluster.local",
        ).rstrip("/")

    def _timeout() -> float:
        return float(os.getenv("DIGITAL_QUALITY_ORCHESTRATOR_CHAT_TIMEOUT_SECONDS", "60"))

    def _resolve_cohort(cohort_id: str) -> Dict[str, Any]:
        if not cohort_id:
            raise HTTPException(status_code=400, detail="cohortId is required")
        try:
            doc = cohorts_helper.get_doc("cohort", cohort_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"cohort lookup failed: {e}")
        if not doc or not isinstance(doc, dict):
            raise HTTPException(status_code=404, detail=f"cohort {cohort_id} not found")
        return doc

    def _resolve_member_bundle(member_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a member id to its stored FHIR Bundle / view, mirroring DEQM's
        ``_deqm_get_patient_bundle`` so the orchestrator receives clinical data
        identical to the per-member ``/fhir/Measure/$evaluate-measure`` path."""
        try:
            doc = cohorts_helper.get_patient(member_id)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(doc, dict) or "error" in doc:
            return None
        nested = doc.get("bundle") if isinstance(doc.get("bundle"), dict) else None
        if nested and nested.get("resourceType") == "Bundle":
            return {"resourceType": "Bundle", **nested}
        return doc

    def _build_member_payload(member_ids: List[str]) -> List[Dict[str, Any]]:
        max_members = int(os.getenv("CHAT_MAX_COHORT_MEMBERS", "25"))
        out: List[Dict[str, Any]] = []
        for mid in member_ids[:max_members]:
            bundle = _resolve_member_bundle(mid)
            if bundle is None:
                continue
            out.append({"patient_id": mid, "bundle": bundle})
        return out

    # ----- POST /cohort-question -----------------------------------------

    @router.post("/cohort-question")
    async def cohort_question(
        payload: CohortQuestionRequest = Body(...),
        current_user: Dict[str, Any] = Depends(auth_dependency),
    ):
        cohort = _resolve_cohort(payload.cohortId)

        # Server-resolved member ids — never trust the client to supply the
        # cohort roster (mitigates lateral lookups across tenants).
        member_ids: List[str] = list(cohort.get("memberIds") or [])
        if not member_ids:
            raise HTTPException(
                status_code=400,
                detail=f"cohort {payload.cohortId} has no members; add members before chatting.",
            )

        cohort_measure_ids: List[str] = list(cohort.get("measureIds") or [])
        selected_measure_ids = payload.selectedMeasureIds or cohort_measure_ids or [
            "CMS122v11",
            "CMS165v9",
            "ePC02",
        ]

        body = {
            "cohort_id": payload.cohortId,
            "cohort_name": cohort.get("name") or payload.cohortId,
            "question": payload.question,
            "intent_hint": payload.intent or "unknown",
            "member_ids": member_ids,
            "members": _build_member_payload(member_ids),
            "measure_ids": selected_measure_ids,
            "period_start": payload.periodStart,
            "period_end": payload.periodEnd,
            "user_principal": {
                "tenantId": current_user.get("tid") or current_user.get("tenant_id"),
                "objectId": current_user.get("oid") or current_user.get("object_id"),
                "upn": current_user.get("upn") or current_user.get("preferred_username"),
            },
        }

        url = f"{_orchestrator_base_url()}/chat/answer"
        try:
            r = requests.post(url, json=body, timeout=_timeout())
        except requests.RequestException as e:
            raise HTTPException(
                status_code=502,
                detail=f"orchestrator unavailable: {e}",
            )

        if r.status_code >= 400:
            # Forward the orchestrator error verbatim so the UI can surface it.
            try:
                detail = r.json()
            except Exception:  # noqa: BLE001
                detail = r.text
            raise HTTPException(status_code=502, detail=detail)

        try:
            return r.json()
        except ValueError:
            raise HTTPException(status_code=502, detail="orchestrator returned non-JSON")

    # ----- GET /episodes/{id}/reward --------------------------------------

    @router.get("/episodes/{episode_id}/reward")
    async def get_episode_reward(
        episode_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        if not episode_id:
            raise HTTPException(status_code=400, detail="episode_id required")
        url = f"{_orchestrator_base_url()}/chat/episodes/{episode_id}/reward"
        try:
            r = requests.get(url, timeout=_timeout())
        except requests.RequestException as e:
            raise HTTPException(
                status_code=502,
                detail=f"orchestrator unavailable: {e}",
            )
        if r.status_code == 404:
            raise HTTPException(status_code=404, detail="episode not found")
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:  # noqa: BLE001
                detail = r.text
            raise HTTPException(status_code=502, detail=detail)
        try:
            return r.json()
        except ValueError:
            raise HTTPException(status_code=502, detail="orchestrator returned non-JSON")

    return router

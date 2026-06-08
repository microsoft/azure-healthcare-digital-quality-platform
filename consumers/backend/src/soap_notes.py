"""
SOAP Notes router (consumers stack)
====================================

Provides patient-centric SOAP-note CRUD persistence for the Consumers
stack frontend. Notes are organized as ``rounds[round_number] = [entries]``
where each entry has a ``role`` (physician / nurse / case-worker) and the
four SOAP sections (subjective / objective / assessment / plan). The
shape matches what ``frontend/src/components/PatientSOAP.tsx`` already
renders, so the UI consumes round-grouped, role-tagged entries without
extra reshaping.

Persistence is best-effort: the router prefers the supplied Cosmos helper
(when ``DOC_TYPE_SOAP_NOTE`` documents are writable), and falls back to a
process-local JSON file under ``SAMPLE_DATA_DIR`` for hosted environments
that have no Cosmos backing or for local development. The fallback path
keeps the contract identical so the frontend never sees a backend swap.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field


DOC_TYPE = "soap_note"


class SoapEntry(BaseModel):
    role: str = Field(..., description="physician | nurse | case-worker | other")
    subjective: Optional[str] = ""
    objective: Optional[str] = ""
    assessment: Optional[str] = ""
    plan: Optional[str] = ""
    encounterId: Optional[str] = None
    author: Optional[str] = None


class SoapNotePayload(BaseModel):
    round: int = Field(1, ge=1)
    entry: SoapEntry


class SoapNoteDoc(BaseModel):
    id: str
    docType: str = DOC_TYPE
    patientId: str
    round: int
    entry: SoapEntry
    createdAt: str
    updatedAt: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _FileStore:
    """JSON file-backed SOAP store. Thread-safe via a single lock."""

    def __init__(self, base_dir: Path) -> None:
        self._lock = threading.Lock()
        self._path = base_dir / "consumers_soap_notes.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: Dict[str, Dict[str, Any]]) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def list_for_patient(self, patient_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            data = self._load()
            return [doc for doc in data.values() if doc.get("patientId") == patient_id]

    def upsert(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            data = self._load()
            data[doc["id"]] = doc
            self._save(data)
            return doc

    def delete(self, note_id: str) -> bool:
        with self._lock:
            data = self._load()
            if note_id in data:
                del data[note_id]
                self._save(data)
                return True
            return False


def create_soap_router(
    *,
    cohorts_helper: Optional[Any],
    auth_dependency: Callable[..., Any],
    sample_data_dir: Path,
) -> APIRouter:
    router = APIRouter(prefix="/api/patients", tags=["soap-notes"])
    store = _FileStore(sample_data_dir)

    def _cosmos_list(patient_id: str) -> Optional[List[Dict[str, Any]]]:
        if cohorts_helper is None:
            return None
        try:
            items = cohorts_helper.query_documents(
                query=f"SELECT * FROM c WHERE c.docType = '{DOC_TYPE}' AND c.patientId = @pid",
                parameters=[{"name": "@pid", "value": patient_id}],
            )
            return list(items or [])
        except Exception:
            return None

    def _cosmos_upsert(doc: Dict[str, Any]) -> bool:
        if cohorts_helper is None:
            return False
        try:
            cohorts_helper.save_patient_data(doc["id"], doc)
            return True
        except Exception:
            return False

    def _cosmos_delete(note_id: str) -> bool:
        if cohorts_helper is None:
            return False
        try:
            cohorts_helper.delete_document(note_id, partition_key=DOC_TYPE)
            return True
        except Exception:
            return False

    @router.get("/{patient_id}/soap-notes")
    async def list_notes(patient_id: str, _user: Any = Depends(auth_dependency)) -> Dict[str, Any]:
        items = _cosmos_list(patient_id)
        if items is None:
            items = store.list_for_patient(patient_id)
        # Group by round so the frontend's PatientSOAP component can consume
        # `{ "1": [entry, entry], "2": [entry] }` directly.
        rounds: Dict[str, List[Dict[str, Any]]] = {}
        for doc in sorted(items, key=lambda d: (d.get("round", 1), d.get("createdAt", ""))):
            rounds.setdefault(str(doc.get("round", 1)), []).append({
                "id": doc.get("id"),
                **(doc.get("entry") or {}),
                "createdAt": doc.get("createdAt"),
                "updatedAt": doc.get("updatedAt"),
            })
        return {"patientId": patient_id, "rounds": rounds, "count": len(items)}

    @router.post("/{patient_id}/soap-notes")
    async def create_note(
        patient_id: str,
        payload: SoapNotePayload,
        _user: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        now = _now_iso()
        doc = {
            "id": f"soap-{patient_id}-{uuid.uuid4().hex[:12]}",
            "docType": DOC_TYPE,
            "patientId": patient_id,
            "round": int(payload.round),
            "entry": payload.entry.model_dump(),
            "createdAt": now,
            "updatedAt": now,
        }
        if not _cosmos_upsert(doc):
            store.upsert(doc)
        return doc

    @router.put("/{patient_id}/soap-notes/{note_id}")
    async def update_note(
        patient_id: str,
        note_id: str,
        payload: SoapNotePayload,
        _user: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        existing = None
        items = _cosmos_list(patient_id)
        if items is not None:
            existing = next((d for d in items if d.get("id") == note_id), None)
        if existing is None:
            existing = next((d for d in store.list_for_patient(patient_id) if d.get("id") == note_id), None)
        if existing is None:
            raise HTTPException(status_code=404, detail="SOAP note not found")
        existing.update({
            "round": int(payload.round),
            "entry": payload.entry.model_dump(),
            "updatedAt": _now_iso(),
        })
        if not _cosmos_upsert(existing):
            store.upsert(existing)
        return existing

    @router.delete("/{patient_id}/soap-notes/{note_id}")
    async def delete_note(
        patient_id: str,
        note_id: str,
        _user: Any = Depends(auth_dependency),
    ) -> Dict[str, Any]:
        deleted = _cosmos_delete(note_id) or store.delete(note_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="SOAP note not found")
        return {"deleted": True, "id": note_id}

    return router

"""Idempotent seeder for the providers Cosmos account.

Reads JSON files from --data-dir (or COSMOS_SEED_DATA_DIR) and upserts
documents into the dq/cohorts and dq/catalog containers. Designed to
run as a one-off Kubernetes Job inside AKS using workload identity.

Files consumed (each optional):
    cohorts.json                -> dq/cohorts (docType=cohort)
    patients.json               -> dq/cohorts (docType=member, save_patient_data shape)
    measures.json               -> dq/catalog (docType=measure)
    measures-tags.json          -> dq/catalog (docType=tag)
    regulatory-agencies.json    -> dq/catalog (docType=agency)
    regulatory-agency-programs.json -> merged into the matching agency.programs[]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from azure.cosmos import CosmosClient, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ! failed to parse {path.name}: {exc}", flush=True)
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _slug(text: str) -> str:
    out = []
    for ch in (text or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("-")
    s = "".join(out)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "item"


def _patient_display(bundle: Dict[str, Any], member_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"bundle": bundle, "mrn": member_id}
    for entry in bundle.get("entry") or []:
        resource = (entry or {}).get("resource") or {}
        if resource.get("resourceType") == "Patient":
            name = (resource.get("name") or [{}])[0] or {}
            payload["patientResourceId"] = resource.get("id")
            payload["displayName"] = (
                " ".join(filter(None, [
                    " ".join(name.get("given") or []),
                    name.get("family", ""),
                ])).strip()
                or member_id
            )
            payload["birthDate"] = resource.get("birthDate")
            payload["gender"] = resource.get("gender")
            break
    return payload


def _upsert_doc(container, doc_type: str, item_id: str, payload: Dict[str, Any]) -> None:
    document = dict(payload)
    document["id"] = item_id
    document["docType"] = doc_type
    container.upsert_item(document)


def _upsert_member(container, member_id: str, payload: Dict[str, Any]) -> None:
    document = dict(payload)
    document["id"] = member_id
    document["mrn"] = member_id
    document["_id"] = member_id
    document.setdefault("docType", "member")
    container.upsert_item(document)


def _seed_catalog_measures(container, data_dir: Path) -> int:
    rows = _read_json(data_dir / "measures.json") or []
    n = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        mid = raw.get("id")
        if not mid:
            continue
        doc = dict(raw)
        doc.setdefault("createdAt", _now_ms())
        doc["updatedAt"] = _now_ms()
        _upsert_doc(container, "measure", mid, doc)
        n += 1
    return n


def _seed_catalog_tags(container, data_dir: Path) -> int:
    rows = _read_json(data_dir / "measures-tags.json") or []
    n = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        tid = raw.get("id") or _slug(raw.get("name", ""))
        if not tid:
            continue
        doc = dict(raw)
        doc.setdefault("color", "#64748b")
        _upsert_doc(container, "tag", tid, doc)
        n += 1
    return n


def _seed_catalog_agencies(container, data_dir: Path) -> int:
    agencies = _read_json(data_dir / "regulatory-agencies.json") or []
    programs = _read_json(data_dir / "regulatory-agency-programs.json") or []
    agency_map: Dict[str, Dict[str, Any]] = {}
    for raw in agencies:
        if not isinstance(raw, dict):
            continue
        aid = raw.get("id") or _slug(raw.get("name", ""))
        if not aid:
            continue
        doc = dict(raw)
        doc["programs"] = []
        agency_map[aid] = doc
    for raw in programs:
        if not isinstance(raw, dict):
            continue
        aid = raw.get("agencyId")
        if not aid or aid not in agency_map:
            continue
        program = {k: v for k, v in raw.items() if k != "agencyId"}
        agency_map[aid]["programs"].append(program)
    for aid, doc in agency_map.items():
        _upsert_doc(container, "agency", aid, doc)
    return len(agency_map)


def _seed_cohorts(container, data_dir: Path) -> int:
    rows = _read_json(data_dir / "cohorts.json") or []
    n = 0
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        cid = raw.get("id") or _slug(raw.get("name", ""))
        if not cid:
            continue
        doc = dict(raw)
        doc.setdefault("createdAt", _now_ms())
        doc["updatedAt"] = _now_ms()
        _upsert_doc(container, "cohort", cid, doc)
        n += 1
    return n


def _seed_members(container, data_dir: Path) -> int:
    rows = _read_json(data_dir / "patients.json") or []
    if isinstance(rows, dict):
        rows = [rows]
    n = 0
    for bundle in rows:
        if not isinstance(bundle, dict):
            continue
        member_id = bundle.get("id") or bundle.get("mrn")
        if not member_id:
            continue
        payload = _patient_display(bundle, member_id)
        _upsert_member(container, member_id, payload)
        n += 1
    return n


def _wait_for_container(client: CosmosClient, db_name: str, container_name: str, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            db = client.get_database_client(db_name)
            c = db.get_container_client(container_name)
            c.read()
            return
        except Exception as exc:
            last_err = exc
            time.sleep(2)
    raise RuntimeError(
        f"Container {db_name}/{container_name} not reachable within {timeout}s: {last_err}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed providers Cosmos containers from JSON files.")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("COSMOS_SEED_DATA_DIR", "/seed-data"),
        help="Directory containing the JSON files (default: /seed-data).",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("COSMOS_ENDPOINT", ""),
        help="Cosmos account endpoint (default: env COSMOS_ENDPOINT).",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("COSMOS_DATABASE", "dq"),
    )
    parser.add_argument(
        "--cohorts-container",
        default=os.environ.get("COSMOS_COHORTS_CONTAINER", "cohorts"),
    )
    parser.add_argument(
        "--catalog-container",
        default=os.environ.get("COSMOS_CATALOG_CONTAINER", "catalog"),
    )
    args = parser.parse_args()

    if not args.endpoint:
        print("ERROR: --endpoint/COSMOS_ENDPOINT is required", file=sys.stderr)
        return 2
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"ERROR: data dir {data_dir} does not exist", file=sys.stderr)
        return 2

    print(f"endpoint  : {args.endpoint}", flush=True)
    print(f"database  : {args.database}", flush=True)
    print(f"cohorts   : {args.cohorts_container}", flush=True)
    print(f"catalog   : {args.catalog_container}", flush=True)
    print(f"data dir  : {data_dir}", flush=True)
    print(f"files     : {sorted(p.name for p in data_dir.iterdir() if p.is_file())}", flush=True)

    credential = DefaultAzureCredential()
    client = CosmosClient(args.endpoint, credential=credential)
    _wait_for_container(client, args.database, args.cohorts_container)
    _wait_for_container(client, args.database, args.catalog_container)

    cohorts = client.get_database_client(args.database).get_container_client(args.cohorts_container)
    catalog = client.get_database_client(args.database).get_container_client(args.catalog_container)

    summary: List[str] = []
    try:
        n = _seed_catalog_measures(catalog, data_dir)
        summary.append(f"measures   : {n}")
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        print(f"FAIL measures: {exc.message}", file=sys.stderr)
        return 1

    try:
        n = _seed_catalog_tags(catalog, data_dir)
        summary.append(f"tags       : {n}")
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        print(f"FAIL tags: {exc.message}", file=sys.stderr)
        return 1

    try:
        n = _seed_catalog_agencies(catalog, data_dir)
        summary.append(f"agencies   : {n}")
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        print(f"FAIL agencies: {exc.message}", file=sys.stderr)
        return 1

    try:
        n = _seed_cohorts(cohorts, data_dir)
        summary.append(f"cohorts    : {n}")
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        print(f"FAIL cohorts: {exc.message}", file=sys.stderr)
        return 1

    try:
        n = _seed_members(cohorts, data_dir)
        summary.append(f"members    : {n}")
    except cosmos_exceptions.CosmosHttpResponseError as exc:
        print(f"FAIL members: {exc.message}", file=sys.stderr)
        return 1

    print("--- seed summary ---", flush=True)
    for line in summary:
        print(line, flush=True)
    print("--- done ---", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

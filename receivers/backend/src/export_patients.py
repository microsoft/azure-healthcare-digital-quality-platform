"""Export all patients from Cosmos DB to a local patients.json file.

Usage (from the backend directory):
    python -m src.export_patients
    python src/export_patients.py --output patients.json

Reads connection settings from the same environment variables used by the
backend service (see .env / .env.sample):
    COSMOS_ENDPOINT or COSMOSDB_ENDPOINT (preferred for AAD auth)
    COSMOSDB_HOST + COSMOSDB_PASSWORD (legacy key auth)
    COSMOSDB_DATABASE (default: clinical)
    COSMOSDB_COLLECTION (default: patients)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

try:
    from dotenv import find_dotenv, load_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

# Allow running both as a module and as a script.
try:
    from .cosmosdb_helper import CosmosDBHelper
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cosmosdb_helper import CosmosDBHelper  # type: ignore


logger = logging.getLogger("export_patients")


def _strip_quotes(value: str) -> str:
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        v = v[1:-1]
    return v


def _build_connection_string() -> str:
    """Resolve a connection string compatible with CosmosDBHelper."""
    endpoint = _strip_quotes(os.getenv("COSMOS_ENDPOINT", "")) or _strip_quotes(
        os.getenv("COSMOSDB_ENDPOINT", "")
    )
    if endpoint:
        return endpoint

    host = _strip_quotes(os.getenv("COSMOSDB_HOST", ""))
    if host:
        # Helper accepts a raw https endpoint; key (if any) is read from env.
        return f"https://{host}:443/"

    raise SystemExit(
        "No Cosmos endpoint configured. Set COSMOS_ENDPOINT or COSMOSDB_HOST."
    )


def export_patients(output_path: Path, page_size: int = 200) -> int:
    database = os.getenv("COSMOSDB_DATABASE", "dq")
    container = (
        os.getenv("COSMOSDB_COHORTS_COLLECTION")
        or os.getenv("COSMOSDB_COLLECTION")  # legacy fallback
        or "cohorts"
    )
    connection_string = _build_connection_string()

    # CosmosDBHelper will pick up COSMOSDB_PASSWORD/COSMOSDB_KEY for raw URL form.
    # The cohorts container is partitioned by /docType; member FHIR bundles
    # live under docType=member.
    helper = CosmosDBHelper(
        connection_string,
        database,
        container,
        partition_key_path="/docType",
        default_partition_value="member",
    )

    logger.info("Querying database=%s container=%s", database, container)

    items: list[dict] = []
    iterator = helper.container.query_items(
        query="SELECT * FROM c",
        enable_cross_partition_query=True,
        max_item_count=page_size,
    )
    for item in iterator:
        # Strip Cosmos system fields for a cleaner export.
        clean = {k: v for k, v in item.items() if not k.startswith("_")}
        items.append(clean)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, default=str)

    logger.info("Wrote %d patient(s) to %s", len(items), output_path)
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export patients from Cosmos DB to JSON.")
    parser.add_argument(
        "--output",
        "-o",
        default="_data/patients.json",
        help="Output file path (default: _data/patients.json)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=200,
        help="Cosmos query page size (default: 200)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    count = export_patients(Path(args.output), page_size=args.page_size)
    print(f"Exported {count} patient(s) to {args.output}")


if __name__ == "__main__":
    main()

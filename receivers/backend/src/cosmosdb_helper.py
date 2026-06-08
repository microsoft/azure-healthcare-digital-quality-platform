import json
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

from azure.cosmos import CosmosClient, ContainerProxy, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


class CosmosDBHelper:
    """Cosmos DB helper for SQL API containers.

    The constructor keeps the existing signature used by main.py. The
    connection_string can be any of the following:
    - Cosmos SQL style: "AccountEndpoint=...;AccountKey=...;..."
    - Mongo style legacy string: "mongodb://user:key@host:10255/?..."
    - Raw endpoint URL when key is provided in COSMOSDB_KEY or COSMOSDB_PASSWORD env vars

    The ``partition_key_path`` and ``default_partition_value`` arguments
    let callers target the new ``dq/catalog`` and ``dq/cohorts``
    containers, which are partitioned by ``/docType``. When a default
    partition value is supplied, every saved document is stamped with
    ``{partition_field: default_partition_value}`` and reads route
    directly to that partition rather than running cross-partition.
    """

    def __init__(
        self,
        connection_string: str,
        database_name: str,
        collection_name: str,
        partition_key_path: str = "/id",
        default_partition_value: Optional[str] = None,
    ):
        endpoint, key = self._resolve_endpoint_and_key(connection_string)

        if not endpoint:
            raise ConnectionError("Cosmos endpoint is missing")

        # Persist partitioning config used by save/read helpers.
        self.partition_key_path = partition_key_path or "/id"
        self.partition_key_field = self.partition_key_path.lstrip("/").split("/")[0] or "id"
        self.default_partition_value = default_partition_value

        last_error: Exception | None = None

        # First attempt key auth when a key is available.
        if key:
            try:
                self.client = CosmosClient(endpoint, credential=key)
                self.database = self.client.get_database_client(database_name)
                self.container: ContainerProxy = self.database.get_container_client(collection_name)
                self.container.read()
                return
            except cosmos_exceptions.CosmosHttpResponseError as e:
                last_error = e
                message = str(getattr(e, "message", e))
                # If local auth is disabled, we should switch to Entra ID auth.
                if "Local Authorization is disabled" not in message:
                    raise ConnectionError(f"Failed to connect to Cosmos DB: {message}") from e
            except Exception as e:
                last_error = e

        # Fallback/primary path: Entra ID (AAD) auth via managed identity or workload identity.
        try:
            aad_credential = DefaultAzureCredential()
            self.client = CosmosClient(endpoint, credential=aad_credential)
            self.database = self.client.get_database_client(database_name)
            self.container = self.database.get_container_client(collection_name)
            self.container.read()
        except cosmos_exceptions.CosmosHttpResponseError as e:
            raise ConnectionError(f"Failed to connect to Cosmos DB with AAD auth: {e.message}") from e
        except Exception as e:
            if last_error is not None:
                raise ConnectionError(
                    f"Failed to connect to Cosmos DB with both key and AAD auth. key_error={last_error}; aad_error={e}"
                ) from e
            raise ConnectionError(f"Unexpected error connecting to Cosmos DB with AAD auth: {e}") from e

    def _resolve_endpoint_and_key(self, connection_string: str) -> tuple[str, str]:
        import os

        raw = (connection_string or "").strip()
        if not raw:
            return "", ""

        if "AccountEndpoint=" in raw and "AccountKey=" in raw:
            parts: Dict[str, str] = {}
            for token in raw.split(";"):
                if "=" not in token:
                    continue
                k, v = token.split("=", 1)
                parts[k.strip()] = v.strip()
            endpoint = parts.get("AccountEndpoint", "")
            key = parts.get("AccountKey", "")
            return endpoint, key

        if raw.startswith("mongodb://"):
            parsed = urlparse(raw)
            host = parsed.hostname or ""
            password = unquote(parsed.password or "")
            endpoint = f"https://{host}:443/" if host else ""
            return endpoint, password

        if raw.startswith("https://"):
            endpoint = raw if raw.endswith("/") else f"{raw}/"
            key = os.getenv("COSMOSDB_KEY", "") or os.getenv("COSMOSDB_PASSWORD", "")
            return endpoint, key

        return "", ""

    def _query_one_by_patient_id(self, patient_id: str) -> Optional[Dict[str, Any]]:
        query = (
            "SELECT TOP 1 * FROM c "
            "WHERE c.id = @patient_id OR c.mrn = @patient_id OR c._id = @patient_id"
        )
        kwargs: Dict[str, Any] = {
            "query": query,
            "parameters": [{"name": "@patient_id", "value": patient_id}],
        }
        if self.default_partition_value is not None:
            kwargs["partition_key"] = self.default_partition_value
        else:
            kwargs["enable_cross_partition_query"] = True
        items = list(self.container.query_items(**kwargs))
        if not items:
            return None
        return items[0]

    def _query_one_by_bundle_patient_id(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """Look inside FHIR Bundle Patient resources for id/identifier matches."""
        query = (
            "SELECT TOP 1 * FROM c "
            "JOIN e IN c.entry "
            "WHERE IS_ARRAY(c.entry) "
            "AND IS_OBJECT(e.resource) "
            "AND e.resource.resourceType = 'Patient' "
            "AND ("
            "e.resource.id = @patient_id "
            "OR EXISTS("
            "SELECT VALUE i FROM i IN e.resource.identifier "
            "WHERE i.value = @patient_id"
            ")"
            ")"
        )
        kwargs: Dict[str, Any] = {
            "query": query,
            "parameters": [{"name": "@patient_id", "value": patient_id}],
        }
        if self.default_partition_value is not None:
            kwargs["partition_key"] = self.default_partition_value
        else:
            kwargs["enable_cross_partition_query"] = True
        items = list(self.container.query_items(**kwargs))
        if not items:
            return None
        return items[0]

    def _candidate_patient_ids(self, patient_id: str) -> list[str]:
        """Generate equivalent id forms (e.g. p-cms122-002 <-> CMS122-002)."""
        raw = (patient_id or "").strip()
        if not raw:
            return []

        candidates: list[str] = [raw]

        # Match p-cms122-002 style ids and add CMS122-002 counterpart.
        prefixed = re.match(r"^p-([a-z]+\d+-\d+)$", raw, flags=re.IGNORECASE)
        if prefixed:
            candidates.append(prefixed.group(1).upper())

        # Match CMS122-002 style ids and add p-cms122-002 counterpart.
        canonical = re.match(r"^([a-z]+\d+-\d+)$", raw, flags=re.IGNORECASE)
        if canonical and not raw.lower().startswith("p-"):
            candidates.append(f"p-{canonical.group(1).lower()}")

        # Deduplicate while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def get_patient_info(self, patient_id: str) -> str:
        """Fetch patient info given a patient id and return JSON text."""
        doc = self.get_patient(patient_id)
        if "error" in doc:
            return f"[No patient found with id: {patient_id}]"
        return json.dumps(doc)

    def get_patient(self, patient_id: str) -> dict:
        """Fetch complete patient object by id/mrn."""
        try:
            partition_value = self.default_partition_value
            for candidate_id in self._candidate_patient_ids(patient_id):
                try:
                    pk = partition_value if partition_value is not None else candidate_id
                    doc = self.container.read_item(item=candidate_id, partition_key=pk)
                    return doc
                except cosmos_exceptions.CosmosResourceNotFoundError:
                    pass

                doc = self._query_one_by_patient_id(candidate_id)
                if doc:
                    return doc

                doc = self._query_one_by_bundle_patient_id(candidate_id)
                if doc:
                    return doc

            return {"error": f"No patient found with MRN: {patient_id}"}
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error("Error fetching patient %s: %s", patient_id, e.message)
            return {"error": f"Database error while fetching patient {patient_id}: {e.message}"}

    def save_patient_data(self, patient_id: str, patient_data: dict):
        """Save complete patient data including demographics, predictions, and digital quality records."""
        try:
            document = dict(patient_data)
            document["id"] = patient_id
            document["mrn"] = patient_id
            document["_id"] = patient_id
            if self.default_partition_value is not None:
                document.setdefault(self.partition_key_field, self.default_partition_value)
            self.container.upsert_item(document)
            return True
        except Exception as e:
            logger.error("Error saving patient data for %s: %s", patient_id, str(e))
            raise

    def save_measurement_result(self, patient_id: str, measurement_record: dict) -> bool:
        """Append a measurement execution record to an existing patient document."""
        try:
            doc = self.get_patient(patient_id)
            if "error" in doc:
                raise ValueError(f"No patient found with MRN: {patient_id}")

            executions = doc.get("measurement_executions") or []
            if not isinstance(executions, list):
                executions = []
            executions.append(measurement_record)

            doc["measurement_executions"] = executions
            doc["last_measurement_result"] = measurement_record
            doc["id"] = patient_id
            doc["mrn"] = patient_id
            doc["_id"] = patient_id
            if self.default_partition_value is not None:
                doc.setdefault(self.partition_key_field, self.default_partition_value)

            self.container.upsert_item(doc)
            return True
        except Exception as e:
            logger.error("Error saving measurement result for %s: %s", patient_id, str(e))
            raise

    # ------------------------------------------------------------------
    # Generic doc-type helpers (for /docType-partitioned containers)
    # ------------------------------------------------------------------

    def upsert_doc(self, doc_type: str, item_id: str, payload: dict) -> dict:
        """Upsert ``payload`` under ``id=item_id`` and ``docType=doc_type``.

        Designed for the new ``dq/catalog`` and ``dq/cohorts`` containers
        which use ``/docType`` as the partition key.
        """
        document = dict(payload)
        document["id"] = item_id
        document[self.partition_key_field] = doc_type
        self.container.upsert_item(document)
        return document

    def get_doc(self, doc_type: str, item_id: str) -> Optional[dict]:
        """Read a document by ``id`` within the ``docType`` partition."""
        try:
            return self.container.read_item(item=item_id, partition_key=doc_type)
        except cosmos_exceptions.CosmosResourceNotFoundError:
            return None
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error("Error reading %s/%s: %s", doc_type, item_id, e.message)
            return None

    def list_docs(self, doc_type: str) -> list[dict]:
        """List all documents in a single ``docType`` partition."""
        try:
            return list(
                self.container.query_items(
                    query="SELECT * FROM c WHERE c.docType = @doc_type",
                    parameters=[{"name": "@doc_type", "value": doc_type}],
                    partition_key=doc_type,
                )
            )
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error("Error listing %s docs: %s", doc_type, e.message)
            return []

    def delete_doc(self, doc_type: str, item_id: str) -> bool:
        """Delete a document by ``id`` within the ``docType`` partition."""
        try:
            self.container.delete_item(item=item_id, partition_key=doc_type)
            return True
        except cosmos_exceptions.CosmosResourceNotFoundError:
            return False
        except cosmos_exceptions.CosmosHttpResponseError as e:
            logger.error("Error deleting %s/%s: %s", doc_type, item_id, e.message)
            raise

import json
import logging
import os
from typing import Any, Dict, List

from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

FOUNDRY_TOKEN_SCOPE = "https://ai.azure.com/.default"


def _to_openai_base_url(endpoint: str) -> str:
    endpoint = (endpoint or "").strip().rstrip("/")
    if not endpoint:
        return ""

    if "/api/projects/" in endpoint:
        endpoint = endpoint.split("/api/projects/")[0]

    if endpoint.endswith("/openai/v1"):
        return f"{endpoint}/"

    return f"{endpoint}/openai/v1/"


def _is_workload_identity_token_failure(error: Exception) -> bool:
    message = str(error)
    return (
        "AADSTS53003" in message
        or "WorkloadIdentityCredential" in message
        or "Conditional Access" in message
    )


class DigitalQualityMeasuresLMCQLExecutor:
    """LLM-based quality-measure planning and evaluation executor."""

    def __init__(self, foundry_project_endpoint: str, model_deployment: str):
        self._foundry_project_endpoint = (foundry_project_endpoint or "").strip()
        self._model_deployment = model_deployment
        self._foundry_api_key = (
            os.getenv("FOUNDRY_API_KEY")
            or os.getenv("AZURE_OPENAI_API_KEY")
            or ""
        ).strip()
        self._exclude_workload_identity = os.getenv(
            "AI_CQL_EXCLUDE_WORKLOAD_IDENTITY", ""
        ).lower() in {"1", "true", "yes", "on"}

    def _get_llm_client(self):
        from azure.ai.projects import AIProjectClient
        from openai import OpenAI

        if not self._foundry_project_endpoint:
            return None

        base_url = _to_openai_base_url(self._foundry_project_endpoint)

        # Break-glass fallback for constrained enterprise tenants where AAD token
        # issuance is blocked by Conditional Access for workload identity.
        if self._foundry_api_key:
            return OpenAI(
                base_url=base_url,
                api_key=self._foundry_api_key,
            )

        credential_kwargs: Dict[str, Any] = {}
        if self._exclude_workload_identity:
            credential_kwargs["exclude_workload_identity_credential"] = True

        # Resolve the managed identity client ID.  The pod may have AZURE_CLIENT_ID
        # empty when AGENT_IDENTITY_APP_ID is unset; fall back to the workload
        # identity provisioned by infra (MCP_SERVER_IDENTITY_CLIENT_ID).
        managed_identity_client_id = (
            os.getenv("AZURE_CLIENT_ID")
            or os.getenv("MCP_SERVER_IDENTITY_CLIENT_ID")
            or ""
        ).strip()
        if managed_identity_client_id:
            credential_kwargs["managed_identity_client_id"] = managed_identity_client_id

        credential = DefaultAzureCredential(**credential_kwargs)

        # Validate the credential eagerly so we can fall back if needed.
        try:
            credential.get_token(FOUNDRY_TOKEN_SCOPE)
        except Exception as auth_error:
            if not self._exclude_workload_identity and _is_workload_identity_token_failure(auth_error):
                logger.warning(
                    "Azure token acquisition failed with workload identity; retrying with "
                    "exclude_workload_identity_credential=True. Error: %s",
                    auth_error,
                )
                retry_kwargs: Dict[str, Any] = {"exclude_workload_identity_credential": True}
                if managed_identity_client_id:
                    retry_kwargs["managed_identity_client_id"] = managed_identity_client_id
                credential = DefaultAzureCredential(**retry_kwargs)
            else:
                raise

        project_client = AIProjectClient(
            endpoint=self._foundry_project_endpoint,
            credential=credential,
        )
        return project_client.get_openai_client()

    def plan_quality_measures(
        self,
        patient_summary: str,
        catalog_ids: List[str],
        catalog_descriptions: Dict[str, str],
        measurement_period: str,
    ) -> List[str]:
        catalog_text = "\n".join(
            f"- {mid}: {catalog_descriptions.get(mid, 'No description')}"
            for mid in catalog_ids
        )

        system_prompt = (
            "You are a clinical quality measure planning agent. "
            "Given a patient's clinical context and a catalog of available eCQM quality measures, "
            "identify which measures are potentially applicable to this patient. "
            "A measure is applicable if the patient could plausibly be in the initial population "
            "(based on age, diagnoses, encounters, observations, and procedures). "
            "Return ONLY a JSON array of measure ID strings. No explanation."
        )

        user_prompt = (
            f"Measurement Period: {measurement_period}\n\n"
            f"Patient Context:\n{patient_summary}\n\n"
            f"Available Measures:\n{catalog_text}\n\n"
            "Which measures should be evaluated for this patient? "
            "Return a JSON array of measure IDs."
        )

        client = self._get_llm_client()
        if client is None:
            logger.warning("No LLM endpoint configured - returning all measures for evaluation")
            return catalog_ids

        try:
            response = client.responses.create(
                model=self._model_deployment,
                instructions=system_prompt,
                input=user_prompt,
                temperature=0,
                max_output_tokens=500,
            )
            content = response.output_text.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            planned = json.loads(content)
            if isinstance(planned, list):
                valid = [m for m in planned if m in catalog_ids]
                logger.info(f"LLM planned measures: {valid}")
                return valid
        except Exception as e:
            logger.error(f"LLM measure planning failed: {e} - falling back to all measures")

        return catalog_ids

    def evaluate_single_measure(
        self,
        measure_def: Any,
        patient_summary: str,
        fhir_context_json: str,
        measurement_period: str,
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a clinical quality measure evaluation engine. "
            "You are given the CQL definition and documentation for a specific eCQM measure, "
            "plus a patient's FHIR R4 clinical data. "
            "Evaluate the measure by determining:\n"
            "1. Whether the patient is in the Initial Population\n"
            "2. Whether the patient is in the Denominator\n"
            "3. Whether any Denominator Exclusions apply (and why)\n"
            "4. Whether the patient is in the Numerator (and why)\n"
            "5. Whether the patient's outcome is 'controlled' (good)\n\n"
            "Return ONLY a JSON object with these exact fields:\n"
            "{\n"
            '  "measure_id": "string",\n'
            '  "measure_name": "string",\n'
            '  "program": "string",\n'
            '  "in_initial_population": bool,\n'
            '  "in_denominator": bool,\n'
            '  "denominator_exclusion": bool,\n'
            '  "denominator_exclusion_reasons": ["string"],\n'
            '  "in_numerator": bool,\n'
            '  "numerator_reasons": ["string"],\n'
            '  "inverse_measure": bool,\n'
            '  "controlled": bool,\n'
            '  "evidence_trace": ["string describing each evaluation step"],\n'
            '  "detail": {}\n'
            "}\n"
            "Be precise. Use the CQL logic exactly. Do not speculate beyond the data provided."
        )

        user_prompt = (
            f"Measurement Period: {measurement_period}\n\n"
            f"=== Measure Documentation (Markdown) ===\n{measure_def.markdown_content[:5000]}\n\n"
            f"=== Patient Context ===\n{patient_summary}\n\n"
            f"=== FHIR Data (JSON) ===\n{fhir_context_json[:6000]}\n\n"
            "Evaluate this measure for this patient. Return JSON only."
        )

        client = self._get_llm_client()
        if client is None:
            return {
                "measure_id": measure_def.measure_id,
                "measure_name": measure_def.measure_name,
                "program": "unknown",
                "in_initial_population": False,
                "in_denominator": False,
                "denominator_exclusion": False,
                "denominator_exclusion_reasons": [],
                "in_numerator": False,
                "numerator_reasons": ["LLM endpoint not configured"],
                "inverse_measure": False,
                "controlled": False,
                "evidence_trace": ["ERROR: No LLM endpoint configured for measure evaluation"],
                "detail": {},
            }

        try:
            response = client.responses.create(
                model=self._model_deployment,
                instructions=system_prompt,
                input=user_prompt,
                temperature=0,
                max_output_tokens=2000,
            )
            content = response.output_text.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result_dict = json.loads(content)
            if not isinstance(result_dict, dict):
                raise ValueError("LLM response must be a JSON object")
            return result_dict
        except Exception as e:
            logger.error(f"LLM measure evaluation failed for {measure_def.measure_id}: {e}")
            return {
                "measure_id": measure_def.measure_id,
                "measure_name": measure_def.measure_name,
                "program": "unknown",
                "in_initial_population": False,
                "in_denominator": False,
                "denominator_exclusion": False,
                "denominator_exclusion_reasons": [],
                "in_numerator": False,
                "numerator_reasons": [f"Evaluation error: {str(e)}"],
                "inverse_measure": False,
                "controlled": False,
                "evidence_trace": [f"ERROR: {str(e)}"],
                "detail": {},
            }

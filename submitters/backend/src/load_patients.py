import json
import requests
import sys
from pathlib import Path
import os
import subprocess
from typing import Optional
import time  # added
# Load .env so os.getenv picks up local secrets/config
try:
	# python -m pip install python-dotenv
	from dotenv import load_dotenv, find_dotenv  # type: ignore
	load_dotenv(find_dotenv(), override=False)
except Exception:
	# Safe no-op if python-dotenv is not installed
	pass

def load_patients_and_save_summaries(json_file_path: str, api_base_url: str = "http://localhost:8000", auth_token: Optional[str] = None, verify: bool = False, verify_timeout: float = 6.0, verify_interval: float = 0.5):
    """
    Load patients from JSON file and save complete patient data to CosmosDB via API
    
    Args:
        json_file_path: Path to the patients.json file
        api_base_url: Base URL of the API (default: http://localhost:8000)
        auth_token: Optional Azure AD Bearer token to authorize requests
        verify: If True, attempts to read back each uploaded patient to confirm persistence
        verify_timeout: Max seconds to wait for each patient's persistence during verification
        verify_interval: Interval between verification retries
    """
    
    # Load the JSON file
    try:
        with open(json_file_path, 'r', encoding='utf-8') as file:
            patients_data = json.load(file)
    except FileNotFoundError:
        print(f"Error: File {json_file_path} not found")
        return
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON format - {e}")
        return
    
    # Ensure patients_data is a list
    if isinstance(patients_data, dict):
        patients_data = [patients_data]
    elif not isinstance(patients_data, list):
        print("Error: JSON should contain a list of patients or a single patient object")
        return
    
    patient_endpoint = f"{api_base_url}/api/patient"
    successful_uploads = 0
    failed_uploads = 0
    accepted_mrns = []  # track MRNs to verify

    # Prepare headers (include Authorization if token provided)
    base_headers = {'Content-Type': 'application/json'}
    if auth_token:
        base_headers['Authorization'] = f"Bearer {auth_token}"
    
    # Process each patient
    for i, patient in enumerate(patients_data):
        try:
            # Use MRN as the canonical identifier for persistence (backend key)
            patient_mrn = patient.get('mrn')
            patient_uuid = patient.get('id')  # optional UUID field in JSON
            patient_name = patient.get('name', 'Unknown')

            if not patient_mrn:
                print(f"Warning: Patient {i+1} ({patient_name}) missing mrn, skipping...")
                failed_uploads += 1
                continue

            if not patient_uuid:
                print(f"Info: Patient {patient_mrn} has no 'id' field; using MRN only.")

            # Payload is the full patient record (contains mrn already)
            payload = patient

            response = requests.post(
                patient_endpoint,
                json=payload,
                headers=base_headers,
                timeout=30
            )

            if 200 <= response.status_code < 300:
                # Try to detect mock DB (no real persistence) by an on-demand GET if verify flag later
                print(f"✓ Saved data for {patient_name} (MRN={patient_mrn}, id={patient_uuid})")
                successful_uploads += 1
                accepted_mrns.append(patient_mrn)
            else:
                print(f"✗ Failed save for {patient_name} (MRN={patient_mrn}): {response.status_code} - {response.text}")
                failed_uploads += 1

        except requests.exceptions.RequestException as e:
            patient_name = patient.get('name', 'unknown')
            print(f"✗ Network error for patient {patient_name} (MRN={patient.get('mrn','?')}): {e}")
            failed_uploads += 1
        except Exception as e:
            patient_name = patient.get('name', 'unknown')
            print(f"✗ Unexpected error for patient {patient_name} (MRN={patient.get('mrn','?')}): {e}")
            failed_uploads += 1
    
    # Print summary
    print(f"\n--- Upload Summary ---")
    print(f"Successful uploads (HTTP 2xx accepted): {successful_uploads}")
    print(f"Failed uploads: {failed_uploads}")
    print(f"Total patients processed: {successful_uploads + failed_uploads}")

    # Optional verification pass
    if verify and accepted_mrns:
        print("\nVerifying persistence of accepted uploads (by MRN)...")
        verified = 0
        missing = []
        for mrn in accepted_mrns:
            if _verify_patient_exists(api_base_url, mrn, base_headers, verify_timeout, verify_interval):
                verified += 1
            else:
                missing.append(mrn)
        print(f"\n--- Verification Summary ---")
        print(f"Verified present: {verified}/{len(accepted_mrns)}")
        if missing:
            print(f"Not found after {verify_timeout}s (MRNs): {', '.join(str(x) for x in missing)}")
            # Heuristic: if everything missing, likely database not configured
            if verified == 0:
                print("\n⚠ All verification attempts failed. Possible causes:\n  - Cosmos DB environment variables not set (see README)\n  - Backend running in mock mode (check server logs for 'Database not configured')\n  - Using wrong API base URL")

def _resolve_bearer_token(provided_token: Optional[str] = None) -> Optional[str]:
    """
    Resolve a Bearer token from:
    1) Explicit value
    2) Environment variables: AZURE_AD_TOKEN, BEARER_TOKEN, ACCESS_TOKEN
    3) Azure CLI using AZURE_TENANT_ID and AZURE_CLIENT_ID
    """
    if provided_token:
        return provided_token.strip()

    for name in ("AZURE_AD_TOKEN", "BEARER_TOKEN", "ACCESS_TOKEN"):
        val = os.getenv(name)
        if val:
            return val.strip()

    tenant = os.getenv("AZURE_TENANT_ID")
    client = os.getenv("AZURE_CLIENT_ID")
    if tenant and client:
        try:
            token = subprocess.check_output(
                [
                    "az", "account", "get-access-token",
                    "--resource", f"api://{client}",
                    "--tenant", tenant,
                    "--query", "accessToken",
                    "-o", "tsv",
                ],
                text=True
            ).strip()
            if token:
                return token
        except Exception:
            pass

    return None

def _verify_patient_exists(api_base_url: str, patient_id: str, headers: dict, timeout_sec: float, interval_sec: float) -> bool:
    """
    Try to GET the patient by ID using common patterns, retrying for a short window.
    Returns True if found (HTTP 200), False otherwise.
    """
    deadline = time.time() + timeout_sec
    endpoints = [
        f"{api_base_url}/api/patient/{patient_id}",
        f"{api_base_url}/api/patient?id={patient_id}",
    ]
    while time.time() < deadline:
        for url in endpoints:
            try:
                r = requests.get(url, headers=headers, timeout=10)
                if r.status_code == 200:
                    return True
                # 404 means not yet persisted or not found; continue until timeout
            except requests.exceptions.RequestException:
                # transient error; retry until timeout
                pass
        time.sleep(interval_sec)
    return False

def main():
    """Main function to run the script"""
    
    # Default path to patients.json in the data/ directory
    default_json_path = "./data/patients.json"
    
    # Check if command line argument provided
    if len(sys.argv) > 1:
        json_file_path = sys.argv[1]
    else:
        json_file_path = str(default_json_path)
    
    # Check if API is accessible
    api_base_url = "http://localhost:8000"
    try:
        response = requests.get(f"{api_base_url}/", timeout=5)
        if response.status_code != 200:
            print(f"Warning: API at {api_base_url} returned status {response.status_code}")
    except requests.exceptions.RequestException:
        print(f"Warning: Cannot connect to API at {api_base_url}. Make sure the FastAPI server is running.")
        choice = input("Continue anyway? (y/n): ")
        if choice.lower() != 'y':
            return

    # Resolve Azure AD token for protected endpoints
    token = _resolve_bearer_token()
    if not token:
        print("No Azure AD token found.")
        print("Set AZURE_AD_TOKEN or BEARER_TOKEN, or run 'az login' and set AZURE_TENANT_ID/AZURE_CLIENT_ID to auto-resolve via Azure CLI.")
        manual = input("Paste a Bearer token to continue (leave blank to abort): ").strip()
        if not manual:
            return
        token = manual

    # Enable verification via flag or env var
    verify_uploads = ("--verify" in sys.argv) or (os.getenv("VERIFY_UPLOADS", "").strip() in ("1", "true", "yes"))

    print(f"Loading patients from: {json_file_path}")
    print(f"API endpoint: {api_base_url}/api/patient")
    if verify_uploads:
        print("Verification: enabled (will read back each patient by ID)\n")
    else:
        print("Verification: disabled (run with --verify or set VERIFY_UPLOADS=1 to enable)\n")

    load_patients_and_save_summaries(json_file_path, api_base_url, auth_token=token, verify=verify_uploads)

if __name__ == "__main__":
    main()

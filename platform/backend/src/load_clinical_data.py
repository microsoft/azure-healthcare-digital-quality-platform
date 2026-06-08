"""
Load FHIR JSON data files into the clinical container via the backend API.

Usage:
    python load_clinical_data.py [DATA_DIR] [--verify]

DATA_DIR defaults to ../../azure-healthcare-digital-quality/data
"""

import json
import glob
import sys
import os
import time
import requests
import subprocess
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv, find_dotenv
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass


def _resolve_bearer_token() -> Optional[str]:
    for name in ("AZURE_AD_TOKEN", "BEARER_TOKEN", "ACCESS_TOKEN"):
        val = os.getenv(name)
        if val:
            return val.strip()

    tenant = os.getenv("AZURE_TENANT_ID")
    client = os.getenv("AZURE_CLIENT_ID")
    if tenant and client:
        try:
            token = subprocess.check_output(
                ["az", "account", "get-access-token",
                 "--resource", f"api://{client}",
                 "--tenant", tenant,
                 "--query", "accessToken", "-o", "tsv"],
                text=True
            ).strip()
            if token:
                return token
        except Exception:
            pass
    return None


def load_clinical_data(data_dir: str, api_base_url: str = "http://localhost:8000",
                       auth_token: Optional[str] = None, verify: bool = False):
    """Load all FHIR JSON files from data_dir into the clinical container."""

    json_files = sorted(glob.glob(os.path.join(data_dir, "*.json")))
    if not json_files:
        print(f"No JSON files found in {data_dir}")
        return

    endpoint = f"{api_base_url}/api/clinical/patients"
    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    success = 0
    failed = 0
    loaded_ids = []

    for filepath in json_files:
        fname = os.path.basename(filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            resp = requests.post(endpoint, json=data, headers=headers, timeout=30)
            if 200 <= resp.status_code < 300:
                member_id = resp.json().get("memberId", "?")
                print(f"✓ {fname} → memberId={member_id}")
                success += 1
                loaded_ids.append(member_id)
            else:
                print(f"✗ {fname}: {resp.status_code} - {resp.text}")
                failed += 1
        except Exception as e:
            print(f"✗ {fname}: {e}")
            failed += 1

    print(f"\n--- Upload Summary ---")
    print(f"Successful: {success}")
    print(f"Failed:     {failed}")
    print(f"Total:      {success + failed}")

    if verify and loaded_ids:
        print("\nVerifying persistence...")
        verified = 0
        for mid in loaded_ids:
            url = f"{api_base_url}/api/clinical/patients/{mid}"
            deadline = time.time() + 6.0
            found = False
            while time.time() < deadline:
                try:
                    r = requests.get(url, headers=headers, timeout=10)
                    if r.status_code == 200:
                        found = True
                        break
                except requests.exceptions.RequestException:
                    pass
                time.sleep(0.5)
            if found:
                verified += 1
            else:
                print(f"  ✗ Not found: {mid}")
        print(f"Verified: {verified}/{len(loaded_ids)}")


def main():
    default_data_dir = str(
        Path(__file__).resolve().parent.parent.parent
        / "azure-healthcare-digital-quality" / "data"
    )
    data_dir = sys.argv[1] if len(sys.argv) > 1 else default_data_dir
    api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")

    if not os.path.isdir(data_dir):
        print(f"Error: data directory not found: {data_dir}")
        sys.exit(1)

    try:
        requests.get(f"{api_base_url}/", timeout=5)
    except requests.exceptions.RequestException:
        print(f"Warning: Cannot connect to API at {api_base_url}.")
        choice = input("Continue anyway? (y/n): ")
        if choice.lower() != "y":
            return

    token = _resolve_bearer_token()
    if not token:
        print("No Azure AD token found. Set AZURE_AD_TOKEN or BEARER_TOKEN.")
        manual = input("Paste a Bearer token (or leave blank to skip auth): ").strip()
        token = manual or None

    verify = ("--verify" in sys.argv) or os.getenv("VERIFY_UPLOADS", "") in ("1", "true")

    print(f"Data directory: {data_dir}")
    print(f"API endpoint:   {api_base_url}/api/clinical/patients")
    print(f"Verification:   {'enabled' if verify else 'disabled'}\n")

    load_clinical_data(data_dir, api_base_url, auth_token=token, verify=verify)


if __name__ == "__main__":
    main()

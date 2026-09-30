"""Regression tests for JWT signature validation in every backend stack."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
STACKS = ("submitters", "receivers", "platform", "providers", "consumers")


@pytest.mark.parametrize("stack", STACKS)
def test_token_requires_valid_signature(stack, monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "test-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "test-client")
    monkeypatch.delenv("BYPASS_TOKEN_VALIDATION", raising=False)

    path = ROOT / stack / "backend" / "src" / "auth_middleware.py"
    spec = importlib.util.spec_from_file_location(f"{stack}_token_validation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    validator = module.AzureADTokenValidator()

    trusted_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(validator, "_get_signing_key", lambda *_: trusted_key.public_key())
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://login.microsoftonline.com/test-tenant/v2.0",
        "aud": "test-client",
        "tid": "test-tenant",
        "exp": now + timedelta(minutes=10),
        "iat": now,
    }
    valid = jwt.encode(claims, trusted_key, algorithm="RS256", headers={"kid": "trusted"})
    forged = jwt.encode(claims, forged_key, algorithm="RS256", headers={"kid": "trusted"})

    assert validator.validate_token(valid)["aud"] == "test-client"
    with pytest.raises(HTTPException) as error:
        validator.validate_token(forged)
    assert error.value.status_code == 401

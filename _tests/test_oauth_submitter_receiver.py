"""Tests for OAuth 2.0 / Microsoft Entra ID Submitter -> Receiver auth (issue #16).

Covers three layers:

1. Receiver token validation helpers
   - tenant allow-list (single-tenant, explicit list, wildcard, ``common``)
   - audience acceptance of ``RECEIVER_APP_ID_URI``
   - application-role helpers (``roles`` claim + delegated ``scp`` scopes)
2. Receiver endpoint authorization
   - ``Receiver.Submit`` required to POST cross-stack DEQM ingest
   - ``Receiver.Read`` required to GET reports
   - missing token -> 401, insufficient role -> 403, valid role -> 200
3. Submitter outbound token acquisition
   - graceful no-op when unconfigured
   - client-credentials scope resolution + bearer header injection
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import Header, HTTPException


# ---------------------------------------------------------------------------
# Lightweight stubs for heavy optional dependencies (mirrors sibling tests)
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_stubs() -> None:
    if "measure_catalog" not in sys.modules:
        mc = _make_stub("measure_catalog")
        mc.DEFAULT_CANONICAL_BASE = "https://example.org/fhir"  # type: ignore[attr-defined]
        mc.get_measure_entry = lambda mid: {"id": mid, "version": "9.0.000"}  # type: ignore[attr-defined]
        mc.list_measures = lambda: []  # type: ignore[attr-defined]
        mc.list_measure_ids = lambda: []  # type: ignore[attr-defined]
    if "cosmosdb_helper" not in sys.modules:
        ch = _make_stub("cosmosdb_helper")
        ch.get_container_client = MagicMock(return_value=None)  # type: ignore[attr-defined]
        ch.CosmosDBHelper = MagicMock  # type: ignore[attr-defined]
    for pkg in ("azure", "azure.cosmos", "azure.identity"):
        if pkg not in sys.modules:
            _make_stub(pkg)
    if "requests" not in sys.modules:
        req = _make_stub("requests")
        req.post = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]
        req.get = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]
        req.RequestException = Exception  # type: ignore[attr-defined]


_ensure_stubs()

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUB_SRC = os.path.join(_BASE, "submitters", "backend", "src")
_REC_SRC = os.path.join(_BASE, "receivers", "backend", "src")


def _load_module(mod_name: str, src_dir: str, filename: str) -> Any:
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(src_dir, filename)
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Receiver auth middleware + workbench router (from the receivers stack).
_rec_auth = _load_module("receivers_auth_middleware", _REC_SRC, "auth_middleware.py")
_rec_wb = _load_module("receivers_workbench", _REC_SRC, "workbench.py")
# Submitter builders + outbound token provider.
_sub_wb = _load_module("submitters_workbench", _SUB_SRC, "workbench.py")
_receiver_auth = _load_module("submitter_receiver_auth", _SUB_SRC, "receiver_auth.py")


# ---------------------------------------------------------------------------
# 1. Receiver token validation helpers
# ---------------------------------------------------------------------------

class TestTenantValidation:
    def _validator(self, monkeypatch, *, allowed: str = "", tenant: str = "home-tenant"):
        monkeypatch.setenv("ENTRA_TENANT_ID", tenant)
        monkeypatch.setenv("ENTRA_CLIENT_ID", "client-abc")
        monkeypatch.setenv("ALLOWED_TENANTS", allowed)
        v = _rec_auth.AzureADTokenValidator()
        v._ensure_initialized()
        return v

    def test_single_tenant_accepts_home_rejects_others(self, monkeypatch):
        v = self._validator(monkeypatch, allowed="")
        assert v._validate_tenant({"tid": "home-tenant"}) is True
        assert v._validate_tenant({"tid": "other-tenant"}) is False

    def test_explicit_allow_list(self, monkeypatch):
        v = self._validator(monkeypatch, allowed="tenant-a, tenant-b")
        assert v._validate_tenant({"tid": "tenant-a"}) is True
        assert v._validate_tenant({"tid": "tenant-b"}) is True
        assert v._validate_tenant({"tid": "home-tenant"}) is False

    def test_wildcard_allows_any_tenant(self, monkeypatch):
        v = self._validator(monkeypatch, allowed="*")
        assert v._validate_tenant({"tid": "any-random-tenant"}) is True

    def test_common_tenant_disables_home_pin(self, monkeypatch):
        v = self._validator(monkeypatch, allowed="", tenant="common")
        assert v._validate_tenant({"tid": "whatever"}) is True


class TestAudienceValidation:
    def test_receiver_app_id_uri_is_accepted(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID", "home-tenant")
        monkeypatch.setenv("ENTRA_CLIENT_ID", "client-abc")
        monkeypatch.setenv("RECEIVER_APP_ID_URI", "api://dq-receiver-api")
        v = _rec_auth.AzureADTokenValidator()
        v._ensure_initialized()
        assert v._validate_audience({"aud": "api://dq-receiver-api"}) is True
        # Bare GUID form also accepted.
        assert v._validate_audience({"aud": "dq-receiver-api"}) is True
        # An unrelated audience is rejected.
        assert v._validate_audience({"aud": "api://some-other-api"}) is False


class TestRoleHelpers:
    def test_roles_claim(self):
        user = {"roles": ["Receiver.Submit"]}
        assert _rec_auth.user_has_role(user, ["Receiver.Submit"]) is True
        assert _rec_auth.user_has_role(user, ["Receiver.Read"]) is False

    def test_any_of_multiple_roles(self):
        user = {"roles": ["Receiver.Admin"]}
        assert _rec_auth.user_has_role(user, ["Receiver.Read", "Receiver.Admin"]) is True

    def test_require_all_roles(self):
        user = {"roles": ["Receiver.Submit"]}
        assert _rec_auth.user_has_role(
            user, ["Receiver.Submit", "Receiver.Read"], require_all=True
        ) is False

    def test_delegated_scopes_are_included(self):
        user = {"scp": "Receiver.Read Receiver.Submit"}
        assert set(_rec_auth.get_user_roles(user)) == {"Receiver.Read", "Receiver.Submit"}
        assert _rec_auth.user_has_role(user, ["Receiver.Submit"]) is True

    def test_no_required_roles_always_passes(self):
        assert _rec_auth.user_has_role({"roles": []}, []) is True


# ---------------------------------------------------------------------------
# 2. Receiver endpoint authorization (via FastAPI TestClient)
# ---------------------------------------------------------------------------

class _MemHelper:
    def __init__(self) -> None:
        self.store: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def upsert_doc(self, dt: str, item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.store.setdefault(dt, {})[item_id] = payload
        return payload

    def get_doc(self, dt: str, item_id: str):
        return self.store.get(dt, {}).get(item_id)

    def list_docs(self, dt: str):
        return list(self.store.get(dt, {}).values())

    def delete_doc(self, dt: str, item_id: str) -> bool:
        return self.store.get(dt, {}).pop(item_id, None) is not None


def _make_role_dep(*required: str):
    """Mimic receivers.main.require_role using the real user_has_role helper.

    Reads the caller's granted roles from an ``X-Test-Roles`` header so the
    test can exercise the router wiring without minting real JWTs.
    """

    async def _dep(x_test_roles: Optional[str] = Header(default=None)) -> Dict[str, Any]:
        if x_test_roles is None:
            raise HTTPException(status_code=401, detail="Authorization header is required")
        user = {"roles": [r for r in x_test_roles.split(",") if r]}
        if required and not _rec_auth.user_has_role(user, list(required)):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return _dep


def _summary_payload() -> Dict[str, Any]:
    return {
        "cohort": {"id": "cohort-test", "name": "Test"},
        "measureIds": ["CMS165v9"],
        "periodStart": "2026-01-01",
        "periodEnd": "2026-12-31",
        "sourceSendId": "mr-send-oauth",
        "perMeasure": [
            {
                "measureId": "CMS165v9",
                "title": "Controlling High Blood Pressure",
                "denominator": 8,
                "numerator": 6,
                "patients": 10,
                "exclusions": 1,
                "performanceRate": 0.75,
            }
        ],
        "perMember": [
            {"memberId": "P001", "perMeasure": [{"measureId": "CMS165v9", "denominator": 1, "numerator": 1}]},
        ],
    }


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    router = _rec_wb.create_workbench_router(
        catalog_helper=_MemHelper(),
        cohorts_helper=_MemHelper(),
        auth_dependency=lambda: {"sub": "test"},
        submit_dependency=_make_role_dep("Receiver.Submit", "Receiver.Admin"),
        read_dependency=_make_role_dep("Receiver.Read", "Receiver.Submit", "Receiver.Admin"),
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestReceiverAuthorization:
    def _report(self) -> Dict[str, Any]:
        return _sub_wb._build_deqm_fhir_payload(_summary_payload(), "summary")

    def test_missing_token_rejected(self, client):
        resp = client.post("/api/workbench/measure-reports", json=self._report())
        assert resp.status_code == 401

    def test_submit_requires_submit_role(self, client):
        # Read-only caller cannot submit.
        resp = client.post(
            "/api/workbench/measure-reports",
            json=self._report(),
            headers={"X-Test-Roles": "Receiver.Read"},
        )
        assert resp.status_code == 403

    def test_submit_with_submit_role_succeeds(self, client):
        resp = client.post(
            "/api/workbench/measure-reports",
            json=self._report(),
            headers={"X-Test-Roles": "Receiver.Submit"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["report"]["reportType"] == "summary"

    def test_admin_can_submit(self, client):
        resp = client.post(
            "/api/workbench/measure-reports",
            json=self._report(),
            headers={"X-Test-Roles": "Receiver.Admin"},
        )
        assert resp.status_code == 200, resp.text

    def test_read_requires_read_role(self, client):
        # Caller with no roles cannot read.
        resp = client.get(
            "/api/workbench/measure-reports",
            headers={"X-Test-Roles": ""},
        )
        assert resp.status_code == 403

    def test_read_with_read_role_succeeds(self, client):
        resp = client.get(
            "/api/workbench/measure-reports",
            headers={"X-Test-Roles": "Receiver.Read"},
        )
        assert resp.status_code == 200, resp.text
        assert "reports" in resp.json()


# ---------------------------------------------------------------------------
# 3. Submitter outbound token acquisition
# ---------------------------------------------------------------------------

class _FakeToken:
    def __init__(self, token: str) -> None:
        self.token = token
        self.expires_on = 9999999999


class _FakeCredential:
    def __init__(self, *args, **kwargs) -> None:  # noqa: D401
        self.args = args
        self.kwargs = kwargs

    def get_token(self, *scopes):  # noqa: D401
        return _FakeToken("fake-access-token")


class TestSubmitterTokenProvider:
    def test_no_config_returns_empty_headers(self, monkeypatch):
        for var in (
            "RECEIVER_APP_ID_URI",
            "RECEIVER_OAUTH_SCOPE",
            "ENTRA_TENANT_ID",
            "ENTRA_CLIENT_ID",
            "AZURE_TENANT_ID",
            "AZURE_CLIENT_ID",
        ):
            monkeypatch.delenv(var, raising=False)
        _receiver_auth.reset_provider()
        assert _receiver_auth.is_configured() is False
        assert _receiver_auth.get_receiver_auth_headers() == {}

    def test_scope_resolution_prefers_explicit_scope(self, monkeypatch):
        monkeypatch.setenv("RECEIVER_APP_ID_URI", "api://dq-receiver-api")
        monkeypatch.setenv("RECEIVER_OAUTH_SCOPE", "api://override/.default")
        _receiver_auth.reset_provider()
        provider = _receiver_auth.ReceiverTokenProvider()
        assert provider.scope == "api://override/.default"

    def test_scope_derived_from_app_id_uri(self, monkeypatch):
        monkeypatch.delenv("RECEIVER_OAUTH_SCOPE", raising=False)
        monkeypatch.setenv("RECEIVER_APP_ID_URI", "api://dq-receiver-api")
        provider = _receiver_auth.ReceiverTokenProvider()
        assert provider.scope == "api://dq-receiver-api/.default"

    def test_client_credentials_headers(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-1")
        monkeypatch.setenv("ENTRA_CLIENT_ID", "submitter-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s3cr3t")
        monkeypatch.setenv("RECEIVER_APP_ID_URI", "api://dq-receiver-api")
        monkeypatch.delenv("RECEIVER_OAUTH_SCOPE", raising=False)
        monkeypatch.delenv("ENTRA_CLIENT_CERTIFICATE_PATH", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_CERTIFICATE_PATH", raising=False)

        # Inject fake azure-identity credential classes into the stub module.
        azure_identity = sys.modules["azure.identity"]
        monkeypatch.setattr(azure_identity, "ClientSecretCredential", _FakeCredential, raising=False)
        monkeypatch.setattr(azure_identity, "CertificateCredential", _FakeCredential, raising=False)
        monkeypatch.setattr(azure_identity, "DefaultAzureCredential", _FakeCredential, raising=False)

        _receiver_auth.reset_provider()
        assert _receiver_auth.is_configured() is True
        headers = _receiver_auth.get_receiver_auth_headers()
        assert headers == {"Authorization": "Bearer fake-access-token"}

    def test_token_failure_degrades_to_empty_headers(self, monkeypatch):
        monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-1")
        monkeypatch.setenv("ENTRA_CLIENT_ID", "submitter-client")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "s3cr3t")
        monkeypatch.setenv("RECEIVER_APP_ID_URI", "api://dq-receiver-api")

        class _BoomCredential(_FakeCredential):
            def get_token(self, *scopes):
                raise RuntimeError("token endpoint unreachable")

        azure_identity = sys.modules["azure.identity"]
        monkeypatch.setattr(azure_identity, "ClientSecretCredential", _BoomCredential, raising=False)
        monkeypatch.setattr(azure_identity, "CertificateCredential", _BoomCredential, raising=False)
        monkeypatch.setattr(azure_identity, "DefaultAzureCredential", _BoomCredential, raising=False)

        _receiver_auth.reset_provider()
        assert _receiver_auth.get_receiver_auth_headers() == {}

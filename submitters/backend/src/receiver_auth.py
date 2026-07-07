"""OAuth 2.0 client-credentials token provider for Submitter -> Receiver calls.

Implements the outbound half of issue #16. When a Submitter dispatches a DEQM
MeasureReport (or the legacy measure-summary payload) to a Receiver, it must
present an OAuth 2.0 access token minted by Microsoft Entra ID so the Receiver
can authenticate the calling organization and authorize the operation.

Design goals
------------
* **Client-credentials flow** — the Submitter is a daemon/service, so it uses
  the app-only flow (no user present). Credentials come from environment
  variables / Key Vault, never from source.
* **Graceful degradation** — if no Entra configuration is present (typical for
  local ``docker compose`` / dev), :func:`get_receiver_auth_headers` returns an
  empty dict so existing unauthenticated flows keep working.
* **No new dependencies** — reuses ``azure-identity`` (already required), which
  wraps MSAL and transparently caches/refreshes tokens.
* **Least privilege / secrets hygiene** — supports client secret, client
  certificate, and managed identity. Prefer certificate or managed identity in
  production; secrets should be sourced from Key Vault.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache a single credential + resolved scope for the process lifetime.
_lock = threading.Lock()
_provider: "Optional[ReceiverTokenProvider]" = None


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable among ``names``."""
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


def _resolve_scope() -> str:
    """Resolve the OAuth scope for the Receiver API.

    Priority:
      1. ``RECEIVER_OAUTH_SCOPE`` (fully-qualified scope, e.g.
         ``api://dq-receiver-api/.default``).
      2. ``RECEIVER_APP_ID_URI`` with ``/.default`` appended.
    Returns an empty string when neither is configured.
    """
    explicit = _env("RECEIVER_OAUTH_SCOPE")
    if explicit:
        return explicit
    app_id_uri = _env("RECEIVER_APP_ID_URI")
    if not app_id_uri:
        return ""
    app_id_uri = app_id_uri.rstrip("/")
    return f"{app_id_uri}/.default"


class ReceiverTokenProvider:
    """Acquire (and cache) Entra ID access tokens for the Receiver API."""

    def __init__(self) -> None:
        self.tenant_id = _env("ENTRA_TENANT_ID", "AZURE_TENANT_ID")
        self.client_id = _env("ENTRA_CLIENT_ID", "AZURE_CLIENT_ID")
        self.client_secret = _env("ENTRA_CLIENT_SECRET", "AZURE_CLIENT_SECRET")
        self.certificate_path = _env(
            "ENTRA_CLIENT_CERTIFICATE_PATH", "AZURE_CLIENT_CERTIFICATE_PATH"
        )
        self.scope = _resolve_scope()
        self._credential = None  # lazily created azure-identity credential

    @property
    def configured(self) -> bool:
        """True when enough config is present to attempt a token request."""
        return bool(self.scope) and bool(self.client_id or self.tenant_id)

    def _build_credential(self):
        """Create the most appropriate azure-identity credential.

        Order of preference:
          1. Client certificate (``ENTRA_CLIENT_CERTIFICATE_PATH``).
          2. Client secret (``ENTRA_CLIENT_SECRET``).
          3. Managed identity / workload identity via ``DefaultAzureCredential``.
        """
        # Imported lazily so unit tests / dev environments without the package
        # installed can still import this module.
        from azure.identity import (
            CertificateCredential,
            ClientSecretCredential,
            DefaultAzureCredential,
        )

        if self.tenant_id and self.client_id and self.certificate_path:
            logger.info("ReceiverTokenProvider: using client certificate credential")
            return CertificateCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                certificate_path=self.certificate_path,
            )
        if self.tenant_id and self.client_id and self.client_secret:
            logger.info("ReceiverTokenProvider: using client secret credential")
            return ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
        logger.info(
            "ReceiverTokenProvider: using DefaultAzureCredential "
            "(managed identity / workload identity)"
        )
        # ``managed_identity_client_id`` lets a user-assigned identity be pinned.
        managed_client_id = _env("AZURE_CLIENT_ID")
        if managed_client_id:
            return DefaultAzureCredential(managed_identity_client_id=managed_client_id)
        return DefaultAzureCredential()

    def get_token(self) -> Optional[str]:
        """Return a bearer token for the Receiver scope, or ``None``.

        ``azure-identity`` caches tokens internally and refreshes them shortly
        before expiry, so it is safe to call this on every outbound request.
        """
        if not self.scope:
            return None
        try:
            if self._credential is None:
                self._credential = self._build_credential()
            token = self._credential.get_token(self.scope)
            return token.token
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ReceiverTokenProvider: failed to acquire access token for scope %s: %s",
                self.scope,
                exc,
            )
            return None


def _get_provider() -> ReceiverTokenProvider:
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                _provider = ReceiverTokenProvider()
    return _provider


def reset_provider() -> None:
    """Reset the cached provider. Intended for tests that mutate the env."""
    global _provider
    with _lock:
        _provider = None


def is_configured() -> bool:
    """Return True when Submitter->Receiver OAuth is configured."""
    return _get_provider().configured


def get_receiver_auth_headers() -> Dict[str, str]:
    """Return an ``Authorization`` header for outbound Receiver calls.

    Returns an empty dict when OAuth is not configured (local/dev) or when
    token acquisition fails, allowing callers to fall back to unauthenticated
    behaviour without raising.
    """
    provider = _get_provider()
    if not provider.configured:
        return {}
    token = provider.get_token()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}

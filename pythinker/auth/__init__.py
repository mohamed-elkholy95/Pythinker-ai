"""Credential resolution for providers.

Single dispatch point for "give me the bearer/API-key for this provider"
regardless of whether it's OAuth-stored, in config.json, or in an env var.
"""

from __future__ import annotations

import os
from typing import Literal

import oauth_cli_kit
from loguru import logger

from pythinker.config.schema import ProviderConfig
from pythinker.providers.registry import ProviderSpec

CredentialSource = Literal["oauth", "config", "env", "none"]


def _has_token(app_name: str, token_filename: str) -> bool:
    """Check if a token exists in storage (fallback for oauth_cli_kit.has_token)."""
    try:
        return oauth_cli_kit.get_token(app_name=app_name, token_filename=token_filename) is not None
    except Exception as exc:
        logger.debug("oauth_cli_kit.get_token lookup failed for {}: {}", app_name, exc)
        return False


def resolve_credential(spec: ProviderSpec, cfg: ProviderConfig) -> str | None:
    """Return the bearer or API-key for a provider, or None.

    Dispatch order:
      1. OAuth provider → oauth_cli_kit token store
      2. cfg.api_key (already ${VAR}-expanded by loader.py)
      3. os.environ[spec.env_key]
    """
    if spec.is_oauth:
        return oauth_cli_kit.get_token(
            app_name=spec.token_app_name or "oauth-cli-kit",
            token_filename=spec.token_filename or "oauth.json",
        )
    if cfg.api_key:
        return cfg.api_key
    if spec.env_key:
        return os.environ.get(spec.env_key)
    return None


def is_authenticated(spec: ProviderSpec, cfg: ProviderConfig) -> bool:
    """Return True if a credential is available for this provider."""
    return resolve_credential(spec, cfg) is not None


def credential_source(spec: ProviderSpec, cfg: ProviderConfig) -> CredentialSource:
    """Return the source of the credential: 'oauth', 'config', 'env', or 'none'."""
    if spec.is_oauth and _has_token(
        app_name=spec.token_app_name or "oauth-cli-kit",
        token_filename=spec.token_filename or "oauth.json",
    ):
        return "oauth"
    if cfg.api_key:
        return "config"
    if spec.env_key and os.environ.get(spec.env_key):
        return "env"
    return "none"

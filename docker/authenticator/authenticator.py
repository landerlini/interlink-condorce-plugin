#!/usr/bin/env python3
"""Obtain an OAuth refresh token via the device-code flow and store it in Kubernetes.

This script is intentionally minimal: it authenticates against an IAM service,
prints the user-facing verification URL and code, poll the token endpoint until the
user authorizes the device, and then stores the resulting refresh token in a
Kubernetes Secret. The code is written to be compatible with Python 3.13 and uses
modern security defaults such as explicit timeout handling and strict validation of
HTTP responses.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Collection
from typing import Any

import kubernetes as k8s
import requests

logger = logging.getLogger(__name__)


def initialize_k8s() -> None:
    """Initialize the Kubernetes client for either in-cluster or local kubeconfig usage."""
    kubeconfig_path = os.environ.get("KUBECONFIG")
    if kubeconfig_path:
        logger.warning("Using local kubeconfig for Kubernetes client configuration.")
        k8s.config.load_kube_config(config_file=kubeconfig_path)
        return

    try:
        k8s.config.load_incluster_config()
    except k8s.config.ConfigException as exc:  # pragma: no cover - depends on runtime environment
        raise RuntimeError(
            "Unable to configure Kubernetes client. Set KUBECONFIG or run inside a cluster."
        ) from exc


def _require_env(name: str) -> str:
    """Return the value of an environment variable or raise a clear error."""
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Environment variable {name!r} is required but not set.")
    return value


def _load_scopes(raw_scopes: str | None) -> list[str]:
    """Parse the configured OAuth scopes, validating that the payload is a list of strings."""
    parsed = json.loads(raw_scopes or '["offline_access", "profile", "openid", "email", "groups"]')
    if not isinstance(parsed, list) or not all(isinstance(scope, str) for scope in parsed):
        raise ValueError("SCOPES must be a JSON list of non-empty strings.")
    return parsed


def get_refresh_token(
    token_endpoint: str,
    devicecode_endpoint: str,
    client_id: str,
    client_secret: str,
    scopes: Collection[str],
    timeout: int = 30,
) -> str:
    """Perform the OAuth device-code flow and return the refresh token.

    The device-code request is intentionally validated before it is used. The refresh
    token is returned as a raw secret string rather than being base64-encoded, because
    base64 is not encryption and should not be treated as a security measure.
    """
    device_request = requests.post(
        devicecode_endpoint,
        data={"client_id": client_id, "scope": " ".join(scopes)},
        timeout=timeout,
    )
    device_request.raise_for_status()

    device_data: dict[str, Any] = device_request.json()
    verification_uri = device_data.get("verification_uri")
    user_code = device_data.get("user_code")
    device_code = device_data.get("device_code")

    if not all(isinstance(value, str) and value for value in (verification_uri, user_code, device_code)):
        raise ValueError("Device-code response is missing required OAuth fields.")

    logger.info("Open %s and enter code %s to complete authentication.", verification_uri, user_code)

    poll_interval = 1
    while True:
        token_response = requests.post(
            token_endpoint,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            auth=(client_id, client_secret),
            timeout=timeout,
        )

        data = token_response.json()
        error = data.get("error")

        if token_response.status_code == 200:
            refresh_token = data.get("refresh_token")
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError("Token response is missing a valid refresh_token.")
            return refresh_token

        if token_response.status_code == 400 and error == "authorization_pending":
            time.sleep(poll_interval)
            continue

        if token_response.status_code == 400 and error == "slow_down":
            poll_interval = min(poll_interval + 5, 30)
            time.sleep(poll_interval)
            continue

        token_response.raise_for_status()


def write_refresh_token_secret(namespace: str, secret_name: str, refresh_token: str) -> None:
    """Persist a refresh token in a Kubernetes Secret using the client's namespace."""
    api = k8s.client.CoreV1Api()
    secret = k8s.client.V1Secret(
        metadata=k8s.client.V1ObjectMeta(name=secret_name, namespace=namespace),
        type="Opaque",
        string_data={"refresh_token": refresh_token},
    )

    try:
        api.read_namespaced_secret(name=secret_name, namespace=namespace)
    except k8s.client.exceptions.ApiException as exc:
        if exc.status == 404:
            api.create_namespaced_secret(namespace=namespace, body=secret)
            return
        raise

    api.patch_namespaced_secret(name=secret_name, namespace=namespace, body=secret)


def main() -> int:
    """Entry point for the IAM authenticator workflow."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    initialize_k8s()

    client_id = _require_env("CLIENT_ID")
    client_secret = _require_env("CLIENT_SECRET")
    devicecode_endpoint = os.environ.get(
        "DEVICECODE_URL",
        "https://iam.cloud.infn.it/devicecode",
    )
    token_endpoint = os.environ.get("TOKEN_URL", "https://iam.cloud.infn.it/token")
    scopes = _load_scopes(os.environ.get("SCOPES"))
    namespace = _require_env("SECRET_NAMESPACE")
    secret_name = _require_env("SECRET_NAME")

    refresh_token = get_refresh_token(
        token_endpoint=token_endpoint,
        devicecode_endpoint=devicecode_endpoint,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )
    write_refresh_token_secret(namespace=namespace, secret_name=secret_name, refresh_token=refresh_token)
    logger.info("Stored refresh token in secret %s/%s.", namespace, secret_name)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.warning("Authentication interrupted by user.")
        raise SystemExit(130)

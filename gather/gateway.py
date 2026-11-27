"""Calls to outside services through Fly.io Sprites connectors.

The Sprite sends no Authorization header. The connector gateway recognizes
the calling Sprite and attaches the stored credential on the way out, so the
agent never holds an API key. These calls only work from inside a running
Sprite that the connector's access policy allows.
"""
import json
import time
import urllib.parse

import requests

TIMEOUT = 60
GATEWAY_LIST_URL = "https://api.sprites.dev/v1/gateway/list"


class GatewayError(RuntimeError):
    pass


class PolicyBlocked(GatewayError):
    """The policy gate refused the request. The message says why."""


def _via_gate(service: str, method: str, path: str, params, body) -> dict:
    from . import config, gate_sign
    import secrets
    payload = json.dumps({"service": service, "method": method, "path": path, "params": params,
                          "body": body, "nonce": secrets.token_hex(8)}).encode()
    headers = {"Content-Type": "application/json", **gate_sign.sign(config.gate_secret(), payload)}
    resp = requests.post(config.gate_url() + "/proxy", data=payload, headers=headers, timeout=TIMEOUT)
    if resp.status_code == 403 and resp.headers.get("Content-Type", "").startswith("application/json") \
            and resp.json().get("policy_gate"):
        raise PolicyBlocked(resp.json().get("error", "blocked by policy gate"))
    if resp.status_code >= 400:
        raise GatewayError(f"gate {method} {path} -> {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else {}


def call(method: str, base: str, path: str, *, params=None, body=None,
         retries: int = 2) -> dict:
    """Send a request through a connector (or the policy gate) and return the JSON."""
    if base.startswith("gate:"):
        return _via_gate(base[5:].strip("/"), method, path, params, body)
    url = base.rstrip("/") + "/" + path.lstrip("/")
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(
                method, url, params=params,
                json=body if body is not None else None,
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:  # network hiccup
            last_error = exc
        else:
            if resp.status_code < 400:
                return resp.json() if resp.content else {}
            # Retry only on rate limits and server errors.
            last_error = GatewayError(
                f"{method} {path} -> {resp.status_code}: {resp.text[:300]}"
            )
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise last_error
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise GatewayError(str(last_error))


def list_connectors() -> dict:
    resp = requests.get(GATEWAY_LIST_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def quote(value: str) -> str:
    """URL-encode a path segment (AgentMail message IDs contain <, @ and >)."""
    return urllib.parse.quote(value, safe="")


def pretty(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)

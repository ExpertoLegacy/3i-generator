"""Talks to the relay. The app holds no AI key, and registration needs no action from the student."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx

from app import net
from app.version import VERSION

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
OFFLINE = "AI needs an internet connection. Your work is saved, and everything else keeps working."


class AIError(Exception):
    """A failure with a message written for the student. Nothing is saved when this is raised."""


def relay_url() -> str:
    configured = os.environ.get("RRG_RELAY_URL")
    if configured is not None:
        return configured.rstrip("/")
    try:
        return json.loads((BASE / "spec" / "relay.json").read_text(encoding="utf-8")).get("url", "").rstrip("/")
    except (OSError, ValueError):
        return ""


class RelayClient:
    def __init__(self, settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(transport=net.transport(), timeout=httpx.Timeout(95, connect=6))

    @property
    def configured(self) -> bool:
        return bool(relay_url())

    def _request(self, method: str, path: str, *, body: dict | None = None, token: str | None = None, timeout: float | None = None) -> httpx.Response:
        headers = {"authorization": f"Bearer {token}"} if token else {}
        options = {"timeout": timeout} if timeout else {}
        try:
            return self.http.request(method, relay_url() + path, json=body, headers=headers, **options)
        except httpx.TransportError as exc:
            detail = net.explain(exc)
            raise AIError(OFFLINE if "internet connection" in detail else f"{detail} Your work is saved, and everything else keeps working.") from exc

    @staticmethod
    def _data(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise AIError("The AI service sent an unreadable reply. Try again in a moment.") from exc
        if response.status_code >= 400 or not data.get("ok"):
            raise AIError(data.get("message") or "The AI service could not finish this request.")
        return data

    def health(self) -> dict:
        return self._data(self._request("GET", "/v1/health", timeout=5))

    def update_available(self, health: dict) -> str:
        """Return the download address when the relay announces a newer version."""
        def numbers(text: str) -> tuple[int, ...]:
            return tuple(int(p) for p in text.split(".") if p.isdigit())

        latest, address = str(health.get("latest_app_version") or ""), str(health.get("download_url") or "")
        return address if address.startswith("https://") and latest and numbers(latest) > numbers(VERSION) else ""

    def _token(self, renew: bool = False) -> str:
        identity = self.settings.data.setdefault("ai", {})
        if not renew and identity.get("token"):
            return identity["token"]
        identity.setdefault("install_id", str(uuid.uuid4()))
        identity["token"] = self._data(self._request("POST", "/v1/register", body={"install_id": identity["install_id"]}, timeout=10))["install_token"]
        self.settings.save()
        return identity["token"]

    def _authorized(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        response = self._request(method, path, body=body, token=self._token(), timeout=timeout)
        if response.status_code == 401:  # the relay's signing secret was rotated
            response = self._request(method, path, body=body, token=self._token(renew=True), timeout=timeout)
        return self._data(response)

    def quota(self) -> dict:
        return self._authorized("GET", "/v1/quota", timeout=8)["quota"]

    def text(self, request: dict) -> dict:
        return self._authorized("POST", "/v1/text", body=request)

    def image(self, request: dict) -> dict:
        return self._authorized("POST", "/v1/image", body=request)

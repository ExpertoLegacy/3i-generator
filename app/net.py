"""Outgoing connections that keep working on school networks."""
from __future__ import annotations

import ssl
from urllib.request import getproxies

import certifi
import httpx


def trusted_context() -> ssl.SSLContext:
    """Trust both the bundled certificates and the computer's own.

    A new Windows install may not have downloaded a root certificate yet, so the bundled list covers
    that. A school firewall that inspects traffic uses its own certificate, which only Windows knows.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_default_certs()
    return context


def transport() -> httpx.HTTPTransport:
    # IPv4 only: on networks without IPv6, Python waits out each IPv6 address first, about 10 seconds.
    # A custom transport makes httpx skip proxy discovery, so the Windows proxy setting is read here.
    return httpx.HTTPTransport(local_address="0.0.0.0", verify=trusted_context(), proxy=getproxies().get("https"))


def explain(error: BaseException) -> str:
    """Put a connection failure in words someone at the school can act on."""
    text = f"{type(error).__name__} {error}".lower()
    if "certificate" in text or "ssl" in text:
        return "A secure connection was refused. Check that this computer's date and time are correct. The school network may also be inspecting traffic."
    if "proxy" in text or "407" in text:
        return "The network's proxy refused the connection. Ask whoever manages the school network to allow the AI service address."
    if "timeout" in text or "timed out" in text:
        return "The connection timed out. The network may be slow or may block the AI service address."
    return "This computer could not reach the AI service. Check the internet connection."

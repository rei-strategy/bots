import os
import httpx
from typing import Any, Dict, List, Optional

QBO_PROXY_URL = os.getenv("QBO_PROXY_URL", "").strip()
QBO_PROXY_BEARER = os.getenv("QBO_PROXY_BEARER", "").strip()
QBO_PROXY_COMMIT = os.getenv("QBO_PROXY_COMMIT", "true").strip().lower() == "true"

class QBOProxyError(RuntimeError):
    pass

def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if QBO_PROXY_BEARER:
        h["Authorization"] = f"Bearer {QBO_PROXY_BEARER}"
    return h

async def create_invoice_via_proxy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post to your webhook/proxy. The proxy owns the real OAuth with QBO and does the creation.
    Expected request body structure (we send):
        {
          "commit": true|false,
          "source": "skye-bot",
          "invoice": { ... normalized invoice payload ... }
        }
    Expected 2xx response JSON with at least:
        {"ok": true, "id": "...", "link": "...", ...}
    """
    if not QBO_PROXY_URL:
        raise QBOProxyError("QBO_PROXY_URL not set")

    body = {
        "commit": QBO_PROXY_COMMIT,
        "source": "skye-bot",
        "invoice": payload
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(QBO_PROXY_URL, headers=_headers(), json=body)
        if r.status_code >= 400:
            raise QBOProxyError(f"Proxy error {r.status_code}: {r.text}")
        try:
            return r.json()
        except Exception:
            raise QBOProxyError(f"Invalid proxy JSON: {r.text}")

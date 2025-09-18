from fastapi import APIRouter, HTTPException, Query, Body
from typing import Any, Dict, Optional
import os
import httpx
from src.providers.qbo_client import _qbo_get  # we reuse the harmless GET ping

router = APIRouter()

# ---------- Helpers ----------
def _proxy_env() -> Dict[str, str]:
    return {
        "url": os.getenv("QBO_PROXY_URL", "").strip(),
        "bearer": os.getenv("QBO_PROXY_BEARER", "").strip(),
    }

def _proxy_headers(bearer: str) -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return h

async def _proxy_post(payload: Dict[str, Any], commit: bool) -> Dict[str, Any]:
    cfg = _proxy_env()
    if not cfg["url"]:
        raise HTTPException(400, "QBO_PROXY_URL not set in .env")
    body = {"commit": bool(commit), "source": "skye-bot", "invoice": payload}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(cfg["url"], headers=_proxy_headers(cfg["bearer"]), json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, f"Proxy error {r.status_code}: {r.text}")
        try:
            return r.json()
        except Exception:
            raise HTTPException(500, f"Invalid proxy JSON: {r.text}")

# ---------- Endpoints ----------

@router.get("/qbo/ping")
async def qbo_ping():
    """
    Harmless GET to confirm OAuth + realm are good (direct Intuit path).
    This does NOT create anything.
    """
    realm = os.getenv("QB_REALM_ID", "").strip()
    if not realm:
        raise HTTPException(400, "QB_REALM_ID not set")
    try:
        data = await _qbo_get(f"companyinfo/{realm}")
        return {"ok": True, "realmId": realm, "companyInfo": data.get("CompanyInfo", {})}
    except Exception as e:
        raise HTTPException(500, f"QBO ping failed: {e}")

@router.post("/qbo/proxy/test")
async def qbo_proxy_test(commit: bool = Query(False, description="If true, marks the payload as commit=true for your Zap")):
    """
    Sends a SAMPLE invoice payload to your QBO proxy (Zapier/Make/Pipedream)
    so you can 'Test trigger' and finish field mappings.
    This DOES NOT call QuickBooks directly.
    """
    sample = {
        "customer_name": "Acme Solar LLC",
        "customer_email": "ap@acmesolar.com",
        "txn_date": "2025-09-12",     # yyyy-mm-dd (optional)
        "due_date": "2025-09-26",     # yyyy-mm-dd (optional)
        "doc_number": "PO-TEST-001",
        "private_note": "Sample note from SkyeLink test",
        "currency": "USD",
        "line_items": [
            {"description": "Flight — 120 MW", "qty": 1, "rate": 2500, "amount": 2500},
            {"description": "Expedite",        "qty": 1, "rate": 350,  "amount": 350}
        ]
    }
    res = await _proxy_post(sample, commit=commit)
    return {"ok": True, "sent": sample, "proxy_response": res, "commit": commit}

@router.post("/qbo/proxy/send")
async def qbo_proxy_send(
    payload: Dict[str, Any] = Body(..., description="Invoice payload to forward to proxy (same shape as the sample)"),
    commit: bool = Query(False, description="If true, marks the payload as commit=true for your Zap"),
):
    """
    Forwards YOUR payload to the proxy. Shape should match:
      {
        "customer_name": "...",
        "customer_email": "...",
        "txn_date": "YYYY-MM-DD",
        "due_date": "YYYY-MM-DD",
        "doc_number": "...",
        "private_note": "...",
        "currency": "USD",
        "line_items": [
          {"description": "...", "qty": 1, "rate": 100, "amount": 100}
        ]
      }
    """
    res = await _proxy_post(payload, commit=commit)
    return {"ok": True, "sent": payload, "proxy_response": res, "commit": commit}

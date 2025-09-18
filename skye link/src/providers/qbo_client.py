import base64
import json
import os
import time
from typing import Any, Dict, List, Optional

import httpx

QB_ENV = os.getenv("QB_ENV", "sandbox").strip().lower()
QB_CLIENT_ID = os.getenv("QB_CLIENT_ID", "")
QB_CLIENT_SECRET = os.getenv("QB_CLIENT_SECRET", "")
QB_REDIRECT_URI = os.getenv("QB_REDIRECT_URI", "http://localhost:8787/qbo/oauth/callback")
QB_REALM_ID = os.getenv("QB_REALM_ID", "")
QB_REFRESH_TOKEN = os.getenv("QB_REFRESH_TOKEN", "")
QB_MINOR_VERSION = os.getenv("QB_MINOR_VERSION", "73")
QB_DEFAULT_ITEM_ID = os.getenv("QB_DEFAULT_ITEM_ID", "1")

_TOKEN_FILE = ".qbo_token.json"

def _intuit_base() -> str:
    return "https://quickbooks.api.intuit.com" if QB_ENV == "production" else "https://sandbox-quickbooks.api.intuit.com"

def _auth_base() -> str:
    return "https://oauth.platform.intuit.com"

def _basic_auth_header() -> str:
    data = f"{QB_CLIENT_ID}:{QB_CLIENT_SECRET}".encode("utf-8")
    return base64.b64encode(data).decode("utf-8")

def _load_token_cache() -> Dict[str, Any]:
    if os.path.exists(_TOKEN_FILE):
        try:
            with open(_TOKEN_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_token_cache(tok: Dict[str, Any]) -> None:
    try:
        with open(_TOKEN_FILE, "w") as f:
            json.dump(tok, f)
    except Exception:
        pass

async def _refresh_access_token() -> str:
    if not (QB_CLIENT_ID and QB_CLIENT_SECRET and QB_REFRESH_TOKEN):
        raise RuntimeError("QBO OAuth env not configured: set QB_CLIENT_ID, QB_CLIENT_SECRET, QB_REFRESH_TOKEN")

    token_cache = _load_token_cache()
    now = int(time.time())
    if token_cache.get("access_token") and token_cache.get("exp") and token_cache["exp"] > now + 60:
        return token_cache["access_token"]

    url = f"{_auth_base()}/oauth2/v1/tokens/bearer"
    headers = {
        "Authorization": f"Basic {_basic_auth_header()}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {"grant_type": "refresh_token", "refresh_token": QB_REFRESH_TOKEN}
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, data=data)
        if r.status_code >= 400:
            raise RuntimeError(f"QBO token refresh failed: {r.status_code} {r.text}")
        resp = r.json()
        access_token = resp["access_token"]
        expires_in = resp.get("expires_in", 3600)
        token_cache["access_token"] = access_token
        token_cache["exp"] = int(time.time()) + int(expires_in) - 30
        _save_token_cache(token_cache)
        return access_token

def _company_url(resource: str) -> str:
    if not QB_REALM_ID:
        raise RuntimeError("QB_REALM_ID not set")
    return f"{_intuit_base()}/v3/company/{QB_REALM_ID}/{resource}?minorversion={QB_MINOR_VERSION}"

async def _qbo_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token = await _refresh_access_token()
    url = _company_url(path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url, headers=headers, params=params)
        if r.status_code >= 400:
            raise RuntimeError(f"QBO GET {path} failed: {r.status_code} {r.text}")
        return r.json()

async def _qbo_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    token = await _refresh_access_token()
    url = _company_url(path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"QBO POST {path} failed: {r.status_code} {r.text}")
        return r.json()

def _qbo_lines_from_simple(lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for li in lines:
        desc = li.get("description", "") or ""
        qty = float(li.get("qty", 1))
        rate = float(li.get("rate", 0))
        amount = float(li.get("amount") or qty * rate)
        out.append({
            "DetailType": "SalesItemLineDetail",
            "Amount": round(amount, 2),
            "Description": desc,
            "SalesItemLineDetail": {
                "ItemRef": {"value": str(QB_DEFAULT_ITEM_ID)},
                "Qty": qty,
                "UnitPrice": rate
            }
        })
    return out

async def _find_customer_by_name(name: str) -> Optional[Dict[str, Any]]:
    safe_name = name.replace("'", "''")
    q = f"select * from Customer where DisplayName = '{safe_name}'"
    data = await _qbo_get("query", params={"query": q})
    ent = (data.get("QueryResponse", {}) or {}).get("Customer", [])
    return ent[0] if ent else None

async def _create_customer(display_name: str, email: Optional[str]) -> Dict[str, Any]:
    payload = {"DisplayName": display_name}
    if email:
        payload["PrimaryEmailAddr"] = {"Address": email}
    data = await _qbo_post("customer", payload)
    return data["Customer"]

async def _qbo_get_customer_or_create(name: str, email: Optional[str]) -> Dict[str, Any]:
    cust = await _find_customer_by_name(name)
    if cust:
        return cust
    return await _create_customer(name, email)

async def create_qbo_invoice(
    customer_name: str,
    customer_email: Optional[str],
    txn_date: Optional[str],
    due_date: Optional[str],
    doc_number: Optional[str],
    private_note: Optional[str],
    line_items: List[Dict[str, Any]],
    currency: str = "USD",
) -> Dict[str, Any]:
    customer = await _qbo_get_customer_or_create(customer_name, customer_email)
    lines = _qbo_lines_from_simple(line_items)

    payload: Dict[str, Any] = {
        "CustomerRef": {"value": str(customer["Id"])},
        "Line": lines,
        "CurrencyRef": {"value": currency},
        # explicitly not emailed / not printed
        "EmailStatus": "NotSet",
        "PrintStatus": "NotSet",
    }
    if txn_date:
        payload["TxnDate"] = txn_date
    if due_date:
        payload["DueDate"] = due_date
    if doc_number:
        payload["DocNumber"] = doc_number
    if private_note:
        payload["PrivateNote"] = private_note[:1000]

    data = await _qbo_post("invoice", payload)
    return data["Invoice"]

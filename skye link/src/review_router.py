from fastapi import APIRouter, HTTPException, Query
from typing import Any, Dict, Optional
import os, time

from src.review_store import queue_list, queue_get, queue_update, queue_delete, queue_clear
from src.providers.qbo_proxy import create_invoice_via_proxy, QBO_PROXY_URL
from src.providers.qbo_client import create_qbo_invoice

router = APIRouter()

def _allow_create() -> bool:
    return os.getenv("QB_ALLOW_CREATE","false").strip().lower()=="true"

@router.get("/review/list")
def review_list():
    return {"ok": True, "items": queue_list()}

@router.get("/review/item/{rid}")
def review_item(rid: str):
    item = queue_get(rid)
    if not item:
        raise HTTPException(404, "Review item not found")
    return {"ok": True, "item": item}

@router.post("/review/reject/{rid}")
def review_reject(rid: str, reason: str = "Rejected by reviewer"):
    item = queue_get(rid)
    if not item:
        raise HTTPException(404, "Review item not found")
    queue_update(rid, {"status": "rejected", "rejected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "reject_reason": reason})
    queue_delete(rid)
    return {"ok": True, "id": rid, "status": "rejected"}

@router.post("/review/approve/{rid}")
async def review_approve(
    rid: str,
    channel: str = Query("proxy", regex="^(proxy|qbo)$"),
    commit: bool = True
):
    """
    Approve and create invoice on purpose.
    channel=proxy -> Zapier/Make/Pipedream webhook
    channel=qbo   -> direct Intuit API (sandbox/prod per env)
    commit=True   -> actually create (if False, dry-run through proxy)
    """
    item = queue_get(rid)
    if not item:
        raise HTTPException(404, "Review item not found")

    if not _allow_create():
        raise HTTPException(400, "QB_ALLOW_CREATE=false — creation not permitted")

    preview = item.get("qbo_preview") or {}
    try:
        if channel == "proxy":
            if not QBO_PROXY_URL:
                raise HTTPException(400, "QBO_PROXY_URL not set for proxy channel")
            res = await create_invoice_via_proxy(preview | {"commit": commit})
            queue_update(rid, {"status":"approved", "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "result": res})
            queue_delete(rid)
            return {"ok": True, "channel":"proxy", "result": res}

        else:
            # direct QBO
            qbo_inv = await create_qbo_invoice(
                customer_name=preview.get("customer_name",""),
                customer_email=preview.get("customer_email"),
                txn_date=preview.get("txn_date"),
                due_date=preview.get("due_date"),
                doc_number=preview.get("doc_number"),
                private_note=preview.get("private_note"),
                line_items=preview.get("line_items",[]),
                currency=preview.get("currency","USD"),
            )
            realm = os.getenv("QB_REALM_ID","")
            env = os.getenv("QB_ENV","sandbox").strip().lower()
            host = "https://app.qbo.intuit.com" if env=="production" else "https://app.sandbox.qbo.intuit.com"
            link = f"{host}/app/invoice?txnId={qbo_inv.get('Id')}&realmId={realm}"
            result = {"ok": True, "qbo_invoice": qbo_inv, "link": link}
            queue_update(rid, {"status":"approved", "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "result": result})
            queue_delete(rid)
            return result

    except Exception as e:
        queue_update(rid, {"status":"error", "error": str(e), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        raise HTTPException(500, f"Approval failed: {e}")

@router.post("/review/clear")
def review_clear():
    queue_clear()
    return {"ok": True, "cleared": True}

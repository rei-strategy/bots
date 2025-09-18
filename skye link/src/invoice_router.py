from __future__ import annotations

import os
from datetime import date
from typing import Literal, Optional, List, Dict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

# Direct QBO client (sandbox/prod via Intuit OAuth)
from src.providers.qbo_client import create_qbo_invoice
# Proxy bridge (Zapier/Make/Pipedream)
from src.providers.qbo_proxy import create_invoice_via_proxy, QBO_PROXY_URL

router = APIRouter()

InvoiceKind = Literal["client", "pilot"]

class LineItem(BaseModel):
    description: str
    qty: float = 1
    rate: float = 0.0
    amount: float | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def auto_amount(cls, v, values):
        try:
            if v is None:
                q = float(values.get("qty", 1))
                r = float(values.get("rate", 0))
                return round(q * r, 2)
        except Exception:
            pass
        return v

class Party(BaseModel):
    name: str
    email: Optional[str] = None
    external_id: Optional[str] = None

class BuildInvoiceIn(BaseModel):
    kind: InvoiceKind
    invoice_date: date
    due_date: Optional[date] = None
    po_number: Optional[str] = None
    ref: Optional[str] = None
    customer: Party
    vendor: Optional[Party] = None
    line_items: List[LineItem]
    notes: Optional[str] = None
    terms: Optional[str] = None
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

class BuiltInvoice(BaseModel):
    kind: InvoiceKind
    header: Dict[str, Optional[str]]
    customer: Party
    vendor: Optional[Party] = None
    line_items: List[LineItem]
    totals: Dict[str, float]
    meta: Dict[str, Optional[str]]

def _normalized_items(items: List[LineItem]) -> List[LineItem]:
    out: List[LineItem] = []
    for li in items:
        amt = li.amount if li.amount is not None else round(float(li.qty) * float(li.rate), 2)
        out.append(LineItem(description=li.description, qty=li.qty, rate=li.rate, amount=amt))
    return out

def _build_totals(items: List[LineItem]) -> Dict[str, float]:
    subtotal = round(sum(float(li.amount or 0.0) for li in items), 2)
    taxes = 0.0
    total = round(subtotal + taxes, 2)
    return {"subtotal": subtotal, "taxes": taxes, "total": total}

def _build_header(in_: BuildInvoiceIn) -> Dict[str, Optional[str]]:
    return {
        "invoice_date": in_.invoice_date.isoformat() if in_.invoice_date else None,
        "due_date": in_.due_date.isoformat() if in_.due_date else None,
        "po_number": in_.po_number,
        "ref": in_.ref,
        "currency": in_.currency,
    }

def _build_meta(in_: BuildInvoiceIn) -> Dict[str, Optional[str]]:
    return {
        "qb_target": "Invoice" if in_.kind == "client" else "Bill",
        "billcom_target": "Invoice" if in_.kind == "client" else "Bill",
        "terms": in_.terms,
        "notes": in_.notes,
    }

def _normalize(in_: BuildInvoiceIn) -> BuiltInvoice:
    items = _normalized_items(in_.line_items)
    header = _build_header(in_)
    meta = _build_meta(in_)
    totals = _build_totals(items)
    return BuiltInvoice(
        kind=in_.kind,
        header=header,
        customer=in_.customer,
        vendor=in_.vendor,
        line_items=items,
        totals=totals,
        meta=meta,
    )

@router.post("/invoice/preview", response_model=BuiltInvoice)
def invoice_preview(in_: BuildInvoiceIn):
    return _normalize(in_)

@router.post("/invoice/build", response_model=BuiltInvoice)
def invoice_build(in_: BuildInvoiceIn):
    return _normalize(in_)

@router.post("/invoice/qbo/create")
async def invoice_qbo_create(
    in_: BuildInvoiceIn,
    commit: bool = Query(False, description="Set true to actually create; otherwise dry-run")
):
    """
    Create invoice via:
      - proxy bridge (if QBO_PROXY_URL is set), OR
      - direct Intuit API (if proxy unset)
    Gates:
      - commit flag (?commit=true)
      - QB_ALLOW_CREATE=true (env) – extra kill-switch
    """
    norm = _normalize(in_)

    allow = os.getenv("QB_ALLOW_CREATE", "false").strip().lower() == "true"
    if not commit or not allow:
        msg = "Set ?commit=true to create in QBO." if not commit else "Creation blocked by QB_ALLOW_CREATE=false."
        return {"ok": True, "dry_run": True, "message": msg, "preview": norm.model_dump()}

    try:
        if QBO_PROXY_URL:
            # Send to your Zapier/Make/Pipedream webhook
            res = await create_invoice_via_proxy({
                "customer_name": norm.customer.name,
                "customer_email": norm.customer.email,
                "txn_date": norm.header["invoice_date"],
                "due_date": norm.header["due_date"],
                "doc_number": norm.header["po_number"] or norm.header["ref"],
                "private_note": norm.meta["notes"],
                "line_items": [li.model_dump() for li in norm.line_items],
                "currency": norm.header["currency"] or "USD",
            })
            return {"ok": True, "dry_run": False, "proxy": res, "preview": norm.model_dump()}
        else:
            # Direct to Intuit (uses your app's keys – sandbox or prod)
            qbo_inv = await create_qbo_invoice(
                customer_name=norm.customer.name,
                customer_email=norm.customer.email,
                txn_date=norm.header["invoice_date"],
                due_date=norm.header["due_date"],
                doc_number=norm.header["po_number"] or norm.header["ref"],
                private_note=norm.meta["notes"],
                line_items=[li.model_dump() for li in norm.line_items],
                currency=norm.header["currency"] or "USD",
            )
            realm = os.getenv("QB_REALM_ID", "")
            env = os.getenv("QB_ENV", "sandbox").strip().lower()
            host = "https://app.qbo.intuit.com" if env == "production" else "https://app.sandbox.qbo.intuit.com"
            link = f"{host}/app/invoice?txnId={qbo_inv.get('Id')}&realmId={realm}"
            return {"ok": True, "dry_run": False, "qbo_invoice": qbo_inv, "link": link, "preview": norm.model_dump()}

    except Exception as e:
        raise HTTPException(500, f"Create invoice failed: {e}")

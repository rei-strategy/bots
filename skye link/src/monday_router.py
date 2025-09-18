from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
import os, httpx, json, re
from datetime import datetime
from pydantic import BaseModel
from typing import Any, Dict, Optional, Tuple

from src.providers.billcom_client import create_vendor_if_needed, create_bill
from src.providers.email_sender import SMTPConfigError, SMTPSendError
from src.providers.qbo_proxy import create_invoice_via_proxy  # <-- LIVE via Zapier/Make/Pipedream ONLY

router = APIRouter()

MONDAY_TOKEN = os.getenv("MONDAY_API_TOKEN", "")
MONDAY_GRAPHQL = "https://api.monday.com/v2"
BOARD_ID = os.getenv("MONDAY_BOARD_ID", "")
HEADERS = {"Authorization": MONDAY_TOKEN, "Content-Type": "application/json"}

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

# ---------------- parsers ----------------
def _parse_money(s: Optional[str]) -> float:
    if not s:
        return 0.0
    txt = s.strip()
    neg = txt.startswith("(") and txt.endswith(")")
    if neg: txt = txt[1:-1]
    m = re.search(r"([+-]?\s*[\d.,]+)\s*([kKmM])\b", txt)
    if m:
        num_part = m.group(1); suf = m.group(2).lower()
        cleaned = re.sub(r"[^\d.,\-]", "", num_part).replace(",", "")
        try:
            base = float(cleaned)
            mult = 1_000 if suf == "k" else 1_000_000
            return -base*mult if neg else base*mult
        except: pass
    cleaned = re.sub(r"[^\d.\-]", "", txt)
    if cleaned.count(".") > 1:
        i = cleaned.find("."); cleaned = cleaned[:i+1] + cleaned[i+1:].replace(".","")
    try:
        val = float(cleaned) if cleaned not in ("", "-", ".", "-.", ".-") else 0.0
        return -val if neg and val>0 else val
    except:
        return 0.0

def _first_money_in_text(s: Optional[str]) -> float:
    if not s: return 0.0
    for t in re.findall(r"\(?\$?\s*[-\d.,]+(?:\s*[kKmM])?\)?", s):
        v = _parse_money(t)
        if v != 0.0: return v
    return 0.0

def _format_date_mmddyyyy(s: Optional[str]) -> Optional[str]:
    if not s or not s.strip(): return None
    txt = s.strip()
    patterns = [
        "%Y-%m-%d","%Y/%m/%d","%m/%d/%Y","%m-%d-%Y","%m/%d/%y","%m-%d-%y",
        "%b %d, %Y","%b %d %Y","%B %d, %Y","%B %d %Y","%d %b %Y","%d %B %Y",
        "%d-%b-%Y","%d-%B-%Y",
    ]
    for p in patterns:
        try: return datetime.strptime(txt,p).strftime("%m-%d-%Y")
        except ValueError: continue
    try:
        dt = datetime.fromisoformat(txt.replace("Z","+00:00")).date()
        return dt.strftime("%m-%d-%Y")
    except: return None

def _to_iso_date(mmddyyyy: Optional[str]) -> Optional[str]:
    if not mmddyyyy: return None
    try: return datetime.strptime(mmddyyyy, "%m-%d-%Y").strftime("%Y-%m-%d")
    except: return None

# ---------------- models ----------------
class APBill(BaseModel):
    vendor_name: str
    invoice_number: Optional[str] = None
    memo: Optional[str] = None
    amount: float
    due_date: Optional[str] = None  # MM-DD-YYYY

# ---------------- Monday GraphQL ----------------
async def monday_graphql(query: str, variables: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not MONDAY_TOKEN:
        raise HTTPException(500, "MONDAY_API_TOKEN not set")
    payload = {"query": query, "variables": variables or {}}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(MONDAY_GRAPHQL, headers=HEADERS, json=payload)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text if e.response is not None else str(e)
        raise HTTPException(502, f"Monday HTTP error: {detail}")
    except Exception as e:
        raise HTTPException(502, f"Monday connection error: {e}")

    if "errors" in data:
        raise HTTPException(400, f"Monday GraphQL errors: {data['errors']}")
    if "data" not in data:
        raise HTTPException(502, f"Unexpected Monday response: {data}")
    return data["data"]

ITEM_QUERY = """
query GetItem($itemId: ID!) {
  items (ids: [$itemId]) {
    id
    name
    board { id }
    column_values { id type text value }
  }
}
"""

BOARD_COLUMNS_QUERY = """
query GetBoardColumns($bid: [ID!]) {
  boards (ids: $bid) { id columns { id title type } }
}
"""

UPDATE_SIMPLE_TEXT = """
mutation SetSimple($board_id: ID!, $item_id: ID!, $column_id: String!, $value: String!) {
  change_simple_column_value(board_id: $board_id, item_id: $item_id, column_id: $column_id, value: $value) { id }
}
"""

UPDATE_JSON_TEXT = """
mutation SetJSON($board_id: ID!, $item_id: ID!, $column_id: String!, $value: JSON!) {
  change_column_value(board_id: $board_id, item_id: $item_id, column_id: $column_id, value: $value) { id }
}
"""

async def get_item(item_id: str) -> Dict[str, Any]:
    data = await monday_graphql(ITEM_QUERY, {"itemId": str(item_id)})
    items = data.get("items", [])
    if not items: raise HTTPException(404, "Item not found")
    return items[0]

async def get_board_columns_map(board_id: str) -> Dict[str, Dict[str,str]]:
    data = await monday_graphql(BOARD_COLUMNS_QUERY, {"bid": [str(board_id)]})
    boards = data.get("boards") or []
    mapping: Dict[str, Dict[str,str]] = {}
    if boards:
        for col in boards[0].get("columns") or []:
            t = _norm(col.get("title")); ctype = col.get("type"); cid = col.get("id")
            if t and cid: mapping[t] = {"id": cid, "type": ctype}
    return mapping

def pick_cv_by_id(column_values: list[Dict[str, Any]], column_id: str) -> dict:
    for c in column_values:
        if c.get("id") == column_id: return c
    return {}

async def resolve_cv(desired: str, board_id: str, cvs: list[Dict[str, Any]]) -> Tuple[dict, str, str]:
    for cv in cvs:
        if _norm(cv.get("id")) == _norm(desired):
            return cv, cv.get("id"), cv.get("type","")
    title_map = await get_board_columns_map(board_id)
    meta = title_map.get(_norm(desired))
    if meta:
        cv = pick_cv_by_id(cvs, meta["id"])
        return cv, meta["id"], meta.get("type","")
    return {}, "", ""

async def set_text_dual(board_id: str, item_id: str, column_id: str, text: str) -> Tuple[bool, Optional[str]]:
    # 1) simple
    try:
        await monday_graphql(UPDATE_SIMPLE_TEXT, {
            "board_id": str(board_id),
            "item_id": str(item_id),
            "column_id": column_id,
            "value": text,
        }); return True, None
    except HTTPException as e1:
        last_err = f"simple:{e1.detail}"
    except Exception as e1:
        last_err = f"simple:{e1}"
    # 2) JSON object
    try:
        await monday_graphql(UPDATE_JSON_TEXT, {
            "board_id": str(board_id),
            "item_id": str(item_id),
            "column_id": column_id,
            "value": {"text": text},
        }); return True, None
    except HTTPException as e2:
        last_err = f"{last_err} | json_obj:{e2.detail}"
    except Exception as e2:
        last_err = f"{last_err} | json_obj:{e2}"
    # 3) JSON-encoded string
    try:
        encoded = json.dumps({"text": text})
        await monday_graphql(UPDATE_JSON_TEXT, {
            "board_id": str(board_id),
            "item_id": str(item_id),
            "column_id": column_id,
            "value": encoded,
        }); return True, None
    except HTTPException as e3:
        last_err = f"{last_err} | json_str:{e3.detail}"
    except Exception as e3:
        last_err = f"{last_err} | json_str:{e3}"
    return False, last_err

# ---------------- build AP bill ----------------
class APBill(BaseModel):
    vendor_name: str
    invoice_number: Optional[str] = None
    memo: Optional[str] = None
    amount: float
    due_date: Optional[str] = None  # MM-DD-YYYY

async def build_apbill_from_item(item: Dict[str, Any]) -> dict:
    cvs = item["column_values"]; board_id = str(item["board"]["id"])

    vendor_key = os.getenv("MONDAY_VENDOR_COLUMN_ID", "text")
    amount_key = os.getenv("MONDAY_AMOUNT_COLUMN_ID", "numbers")
    due_key    = os.getenv("MONDAY_DUE_DATE_COLUMN_ID", "date")
    inv_key    = os.getenv("MONDAY_INVOICE_NUM_COLUMN_ID", "text6")
    memo_key   = os.getenv("MONDAY_MEMO_COLUMN_ID", "text7")

    vendor_cv, vendor_id, _type_v = await resolve_cv(vendor_key, board_id, cvs)
    amount_cv, amount_id, _type_a = await resolve_cv(amount_key, board_id, cvs)
    due_cv,    due_id,    _type_d = await resolve_cv(due_key,    board_id, cvs)
    inv_cv,    inv_id,    _type_i = await resolve_cv(inv_key,    board_id, cvs)
    memo_cv,   memo_id,   _type_m = await resolve_cv(memo_key,   board_id, cvs)

    vendor_text = (vendor_cv.get("text") or item["name"] or "").strip()

    raw_amount_text = amount_cv.get("text") or ""
    parsed_amount = _parse_money(raw_amount_text)
    if parsed_amount == 0.0:
        inv_txt = inv_cv.get("text") or ""
        v = _first_money_in_text(inv_txt);  parsed_amount = v if v != 0.0 else parsed_amount
    if parsed_amount == 0.0:
        memo_txt = memo_cv.get("text") or ""
        v = _first_money_in_text(memo_txt); parsed_amount = v if v != 0.0 else parsed_amount
    if parsed_amount == 0.0:
        for cv in cvs:
            v = _first_money_in_text(cv.get("text") or "")
            if v != 0.0: parsed_amount = v; break

    raw_due = due_cv.get("text") or None
    formatted_due = _format_date_mmddyyyy(raw_due) if raw_due else None
    invnum = inv_cv.get("text") or None
    memo_text = memo_cv.get("text") or None

    apbill = APBill(
        vendor_name=vendor_text,
        amount=parsed_amount,
        due_date=formatted_due,
        invoice_number=invnum,
        memo=memo_text,
    )

    diagnostics = {
        "board_id": board_id,
        "item_id": item["id"],
        "resolved_columns": {
            "vendor": {"env": vendor_key, "id": vendor_id, "text": vendor_text},
            "amount": {"env": amount_key, "id": amount_id, "text": raw_amount_text, "parsed_amount": parsed_amount},
            "due":    {"env": due_key,    "id": due_id,    "text": raw_due, "formatted_mmddyyyy": formatted_due},
            "inv":    {"env": inv_key,    "id": inv_id,    "text": invnum},
            "memo":   {"env": memo_key,   "id": memo_id,   "text": memo_text},
        },
        "apbill": apbill.model_dump(),
        "validation": {
            "has_vendor": bool(vendor_text),
            "amount_gt_zero": parsed_amount > 0,
            "memo_column_resolved": True,
        }
    }
    return {"apbill": apbill, "diag": diagnostics, "memo_col_id": memo_id or ""}

# ---------------- gates (live) ----------------
def _gates_now() -> Dict[str, Any]:
    return {
        "MONDAY_QBO_CREATE": (os.getenv("MONDAY_QBO_CREATE","false").strip().lower()=="true"),
        "MONDAY_QBO_COMMIT": (os.getenv("MONDAY_QBO_COMMIT","false").strip().lower()=="true"),
        "QB_ALLOW_CREATE":   (os.getenv("QB_ALLOW_CREATE","false").strip().lower()=="true"),
        "QBO_PROXY_URL_SET": bool(os.getenv("QBO_PROXY_URL","").strip()),
    }

@router.get("/monday/gates")
def monday_gates():
    return {"ok": True, **_gates_now()}

# ---------------- webhook ----------------
@router.get("/monday/webhook")
async def monday_webhook_get():
    return PlainTextResponse("ok", status_code=200)

@router.post("/monday/webhook")
async def monday_webhook(req: Request):
    raw = await req.body()
    try: payload = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception: payload = {}

    # verification
    if isinstance(payload, dict) and "challenge" in payload:
        return PlainTextResponse(payload["challenge"], status_code=200)

    event = payload.get("event", {})
    if not event: return JSONResponse({"ok": True})

    # Optional status label filter
    trig = os.getenv("MONDAY_TRIGGER_STATUS_LABEL","").strip().lower()
    if trig:
        label_txt = ((event.get("value") or {}).get("label") or {}).get("text","")
        if (label_txt or "").strip().lower() != trig:
            return JSONResponse({"ok": True, "ignored": f"status '{label_txt}'"})

    item_id = str(event.get("pulseId") or event.get("itemId") or "")
    board_id = str(event.get("boardId") or "")
    if not item_id: return JSONResponse({"ok": True})
    if BOARD_ID and board_id and board_id != str(BOARD_ID): return JSONResponse({"ok": True})

    # Build bill
    item = await get_item(item_id)
    built = await build_apbill_from_item(item)
    apbill: APBill = built["apbill"]
    memo_col_id = built["memo_col_id"]
    gates = _gates_now()

    # Email + Memo
    try:
        vendor_id = await create_vendor_if_needed(apbill.vendor_name)
        bill_id = await create_bill(
            vendor_id=vendor_id,
            amount=apbill.amount,
            due_date=apbill.due_date,
            invoice_number=apbill.invoice_number,
            memo=apbill.memo,
        )
        _ok, _err = await set_text_dual(board_id, item_id, memo_col_id, "Invoice Sent")
        email_part = {"email_ok": True, "email_id": bill_id, "memo_update_ok": _ok, "memo_error": _err}
    except (SMTPConfigError, SMTPSendError) as e:
        _ok, _err = await set_text_dual(board_id, item_id, memo_col_id, f"Bill EMAIL ERROR: {e}")
        return JSONResponse({"ok": False, "email_error": str(e), "memo_update_ok": _ok, "memo_error": _err}, status_code=500)
    except Exception as e:
        _ok, _err = await set_text_dual(board_id, item_id, memo_col_id, f"Bill ERROR: {e}")
        return JSONResponse({"ok": False, "error": str(e), "memo_update_ok": _ok, "memo_error": _err}, status_code=500)

    # QBO creation (LIVE via PROXY ONLY)
    if not gates["MONDAY_QBO_CREATE"]:
        return JSONResponse({"ok": True, **email_part, "qbo": {"enabled": False}, "gates": gates})

    if not gates["QBO_PROXY_URL_SET"]:
        return JSONResponse({"ok": False, **email_part, "qbo_error": "QBO_PROXY_URL not set", "gates": gates}, status_code=400)

    # Prepare normalized proxy payload
    desc = item["name"]
    if apbill.invoice_number:
        desc = f"{desc} — {apbill.invoice_number}"
    qbo_payload = {
        "customer_name": apbill.vendor_name,
        "customer_email": None,
        "txn_date": None,  # let QBO default to today
        "due_date": _to_iso_date(apbill.due_date) if apbill.due_date else None,
        "doc_number": apbill.invoice_number,
        "private_note": apbill.memo or "",
        "currency": "USD",
        "line_items": [
            {"description": desc, "qty": 1, "rate": float(apbill.amount), "amount": float(apbill.amount)}
        ]
    }

    # Dry-run?
    if not gates["MONDAY_QBO_COMMIT"] or not gates["QB_ALLOW_CREATE"]:
        # Send preview to proxy if you want Zap to filter on commit=false
        try:
            res = await create_invoice_via_proxy(qbo_payload | {"commit": False})
            return JSONResponse({"ok": True, **email_part, "qbo": {"enabled": True, "dry_run": True, "proxy": res}, "gates": gates})
        except Exception as e:
            return JSONResponse({"ok": False, **email_part, "qbo_error": str(e), "preview": qbo_payload, "gates": gates}, status_code=500)

    # Commit
    try:
        res = await create_invoice_via_proxy(qbo_payload | {"commit": True})
        return JSONResponse({"ok": True, **email_part, "qbo": {"enabled": True, "dry_run": False, "proxy": res}, "gates": gates})
    except Exception as e:
        return JSONResponse({"ok": False, **email_part, "qbo_error": str(e), "preview": qbo_payload, "gates": gates}, status_code=500)

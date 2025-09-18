import os
from typing import Optional
from src.providers.email_sender import send_email

BILLCOM_BASE = os.getenv("BILLCOM_API_BASE", "").rstrip("/")
BILLCOM_KEY  = os.getenv("BILLCOM_API_KEY")
BILLCOM_ORG  = os.getenv("BILLCOM_ORG_ID")
BILLCOM_EMAIL_INBOX = os.getenv("BILLCOM_EMAIL_INBOX")

def _format_currency(amount: float) -> str:
    return "${:,.2f}".format(amount or 0.0)

async def create_vendor_if_needed(vendor_name: str) -> str:
    """
    Placeholder for Bill.com vendor search/create.
    Returns a deterministic vendor id so the rest of the flow works.
    """
    if not vendor_name:
        raise ValueError("vendor_name required")
    return f"vendor_{vendor_name.lower().replace(' ', '_')}"

async def create_bill(
    vendor_id: str,
    amount: float,
    due_date: Optional[str] = None,
    invoice_number: Optional[str] = None,
    memo: Optional[str] = None,
) -> str:
    """
    Gmail fallback: email a tidy AP summary to the configured inbox.
    Later, replace with a real Bill.com API call.
    """
    recipient = BILLCOM_EMAIL_INBOX
    if not recipient:
        raise RuntimeError("No BILLCOM_EMAIL_INBOX configured.")

    subject = f"[AP] {invoice_number or 'No Inv #'} — {vendor_id} — {_format_currency(amount)}"
    html = f"""
    <div style="font-family:Inter,Arial,sans-serif;font-size:14px;line-height:1.6">
      <h2 style="margin:0 0 8px">Accounts Payable — New Bill</h2>
      <table cellspacing="0" cellpadding="6" border="0" style="border-collapse:collapse">
        <tr><td><b>Vendor</b></td><td>{vendor_id}</td></tr>
        <tr><td><b>Invoice #</b></td><td>{invoice_number or '-'}</td></tr>
        <tr><td><b>Amount</b></td><td>{_format_currency(amount)}</td></tr>
        <tr><td><b>Due Date</b></td><td>{due_date or '-'}</td></tr>
        <tr><td><b>Memo</b></td><td>{memo or '-'}</td></tr>
      </table>
      <p style="color:#666">This is a Gmail fallback. Replace with Bill.com API when credentials are ready.</p>
    </div>
    """.strip()

    # Send the email (SMTP must be configured in .env)
    send_email(subject, html, to=recipient)

    # Return a synthetic "email id" so upstream can log something deterministic
    return f"email_{vendor_id}_{(invoice_number or 'noinv').replace(' ', '_')}"
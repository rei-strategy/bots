from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
import os, base64, httpx

router = APIRouter()

AUTH_BASE = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPES = "com.intuit.quickbooks.accounting openid profile email phone address"

def _env():
    return {
        "client_id": os.getenv("QB_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("QB_CLIENT_SECRET", "").strip(),
        "redirect_uri": os.getenv("QB_REDIRECT_URI", "http://localhost:8787/qbo/oauth/callback").strip(),
    }

def _basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")

@router.get("/qbo/oauth/start")
def qbo_oauth_start():
    cfg = _env()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise HTTPException(400, "QB_CLIENT_ID / QB_CLIENT_SECRET not set")
    qp = httpx.QueryParams({
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": SCOPES,
        "state": "xyz",
    })
    return RedirectResponse(url=f"{AUTH_BASE}?{qp}")

@router.get("/qbo/oauth/callback")
async def qbo_oauth_callback(code: str, realmId: str = "", state: str = ""):
    cfg = _env()
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise HTTPException(400, "QB_CLIENT_ID / QB_CLIENT_SECRET not set")

    headers = {
        "Authorization": f"Basic {_basic_auth(cfg['client_id'], cfg['client_secret'])}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg["redirect_uri"]}

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(TOKEN_URL, headers=headers, data=data)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        tokens = r.json()

    return JSONResponse({
        "ok": True,
        "realmId": realmId,
        "access_token_expires_in": tokens.get("expires_in"),
        "refresh_token_expires_in": tokens.get("x_refresh_token_expires_in"),
        "paste_into_env": {
            "QB_REALM_ID": realmId,
            "QB_REFRESH_TOKEN": tokens.get("refresh_token"),
        }
    })

from fastapi import APIRouter, Request

router = APIRouter()

@router.post("/webhook/test")
async def webhook_test(req: Request):
    """
    Echo back whatever JSON body was posted.
    Useful to confirm external webhooks can reach your server.
    """
    try:
        body = await req.json()
    except Exception:
        body = {"_note": "non-JSON body"}
    if isinstance(body, dict) and "challenge" in body:
        return {"challenge": body["challenge"]}
    return {"ok": True, "received": body}
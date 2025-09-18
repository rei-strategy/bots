from pydantic import BaseModel
from fastapi import APIRouter
import os, httpx

router = APIRouter()
MODEL = os.getenv("MODEL", "gpt-4.1-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")

class ChatIn(BaseModel):
    message: str
    system: str | None = None

@router.post("/chat")
async def chat(in_: ChatIn):
    system = in_.system or "You are a focused systems + automation assistant."
    headers = {"Authorization": f"Bearer {OPENAI_KEY}"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": in_.message}
        ]
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(OPENAI_URL, headers=headers, json=payload)
        r.raise_for_status()
        return {"reply": r.json()["choices"][0]["message"]["content"]}
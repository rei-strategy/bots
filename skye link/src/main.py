import os
from fastapi import FastAPI
from dotenv import load_dotenv

# Load env
load_dotenv()

from src.router import router as base_router
from src.invoice_router import router as invoice_router
from src.monday_router import router as monday_router
from src.debug_router import router as debug_router
from src.qbo_oauth_router import router as qbo_oauth_router
from src.qbo_router import router as qbo_router
from src.review_router import router as review_router  # <-- add

app = FastAPI(title="SkyeLink AP Bot")

# API routes
app.include_router(base_router,    prefix="/api")
app.include_router(invoice_router, prefix="/api")
app.include_router(monday_router,  prefix="/api")
app.include_router(debug_router,   prefix="/api")
app.include_router(qbo_router,     prefix="/api")
app.include_router(review_router,  prefix="/api")   # <-- add

# OAuth (no /api prefix)
app.include_router(qbo_oauth_router)

@app.get("/health")
def health():
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True
    )

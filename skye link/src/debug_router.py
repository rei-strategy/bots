from fastapi import APIRouter, HTTPException
import os, smtplib, socket
from email.mime.text import MIMEText
from email.utils import formataddr

router = APIRouter()

def _env():
    return {
        "SMTP_HOST": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "SMTP_PORT": int(os.getenv("SMTP_PORT", "587")),
        "SMTP_USERNAME": os.getenv("SMTP_USERNAME"),
        "SMTP_PASSWORD_SET": bool(os.getenv("SMTP_PASSWORD")),
        "SMTP_FROM": os.getenv("SMTP_FROM") or os.getenv("SMTP_USERNAME"),
        "SMTP_TO_DEFAULT": os.getenv("SMTP_TO_DEFAULT"),
    }

def _connect_only(host: str, port: int, timeout: float = 5.0):
    with socket.create_connection((host, port), timeout=timeout) as s:
        return {"ok": True, "peer": s.getpeername()}

def _login_only(host: str, port: int, user: str, password: str, timeout: float = 8.0):
    with smtplib.SMTP(host, port, timeout=timeout) as s:
        code, hello = s.ehlo()
        if code >= 400:
            return {"ok": False, "step": "EHLO(pre)", "code": code, "detail": str(hello)}
        code, _ = s.starttls()
        if code != 220:
            return {"ok": False, "step": "STARTTLS", "code": code}
        code, hello2 = s.ehlo()
        if code >= 400:
            return {"ok": False, "step": "EHLO(post)", "code": code, "detail": str(hello2)}
        s.login(user, password)
        return {"ok": True}

def _send_test(host: str, port: int, user: str, password: str, from_addr: str, to_addr: str, timeout: float = 8.0):
    msg = MIMEText("<p>SMTP test email from SkyeLink AP Bot</p>", "html", "utf-8")
    msg["Subject"] = "SMTP Test OK"
    msg["From"] = formataddr(("SkyeLink AP Bot", from_addr))
    msg["To"] = to_addr
    with smtplib.SMTP(host, port, timeout=timeout) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, password)
        s.sendmail(from_addr, [to_addr], msg.as_string())
        return {"ok": True}

@router.get("/debug/smtp/ping")
def smtp_ping():
    e = _env()
    try:
        r = _connect_only(e["SMTP_HOST"], e["SMTP_PORT"])
        return {"ok": True, "host": e["SMTP_HOST"], "port": e["SMTP_PORT"], "peer": r["peer"]}
    except Exception as ex:
        raise HTTPException(500, f"PING failed: {type(ex).__name__}: {ex}")

@router.get("/debug/smtp/login")
def smtp_login():
    e = _env()
    missing = [k for k in ("SMTP_USERNAME", "SMTP_PASSWORD_SET", "SMTP_FROM") if not e[k]]
    if missing:
        raise HTTPException(400, f"Missing SMTP env(s): {', '.join(missing)}")
    try:
        r = _login_only(e["SMTP_HOST"], e["SMTP_PORT"], e["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD"))
        return {"ok": True, "host": e["SMTP_HOST"], "port": e["SMTP_PORT"], "login": r}
    except smtplib.SMTPAuthenticationError as ex:
        raise HTTPException(401, f"AUTH failed: {ex}")
    except Exception as ex:
        raise HTTPException(500, f"LOGIN failed: {type(ex).__name__}: {ex}")

@router.get("/debug/smtp/send")
def smtp_send():
    e = _env()
    missing = [k for k in ("SMTP_USERNAME", "SMTP_PASSWORD_SET", "SMTP_FROM") if not e[k]]
    if missing:
        raise HTTPException(400, f"Missing SMTP env(s): {', '.join(missing)}")
    to_addr = e["SMTP_TO_DEFAULT"] or e["SMTP_FROM"]
    try:
        r = _send_test(
            e["SMTP_HOST"], e["SMTP_PORT"], e["SMTP_USERNAME"], os.getenv("SMTP_PASSWORD"),
            e["SMTP_FROM"], to_addr
        )
        return {"ok": True, "to": to_addr, "sent": r}
    except smtplib.SMTPAuthenticationError as ex:
        raise HTTPException(401, f"AUTH failed: {ex}")
    except Exception as ex:
        raise HTTPException(500, f"SEND failed: {type(ex).__name__}: {ex}")
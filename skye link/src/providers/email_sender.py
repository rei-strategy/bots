import os
import smtplib
import socket
from email.mime.text import MIMEText
from email.utils import formataddr

class SMTPConfigError(RuntimeError): ...
class SMTPSendError(RuntimeError): ...

def _settings():
    # Read env at CALL TIME (so .env changes and load order don’t bite us)
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    user = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    from_addr = os.getenv("SMTP_FROM", user)
    to_default = os.getenv("SMTP_TO_DEFAULT")
    return host, port, user, password, from_addr, to_default

def _ensure_config(user, password, from_addr, to_default):
    missing = [k for k, v in {
        "SMTP_USERNAME": user,
        "SMTP_PASSWORD": password,
        "SMTP_FROM": from_addr,
    }.items() if not v]
    if missing:
        raise SMTPConfigError(f"Missing SMTP env(s): {', '.join(missing)}")
    if not to_default:
        # Not fatal, you can still pass `to=` explicitly
        pass

def send_email(subject: str, html_body: str, to: str | None = None):
    """
    Sends an HTML email via STARTTLS (port 587 default).
    Reads SMTP_* env at call time.
    """
    host, port, user, password, from_addr, to_default = _settings()
    _ensure_config(user, password, from_addr, to_default)
    to_addr = to or to_default
    if not to_addr:
        raise SMTPConfigError("No recipient configured: set SMTP_TO_DEFAULT or pass 'to'")

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = formataddr(("SkyeLink AP Bot", from_addr))
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            code, hello = s.ehlo()
            if code >= 400:
                raise SMTPSendError(f"EHLO failed: {code} {hello}")

            code, starttls = s.starttls()
            if code != 220:
                raise SMTPSendError(f"STARTTLS failed: {code} {starttls}")

            code, hello2 = s.ehlo()
            if code >= 400:
                raise SMTPSendError(f"EHLO (post TLS) failed: {code} {hello2}")

            s.login(user, password)
            s.sendmail(from_addr, [to_addr], msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise SMTPSendError(f"SMTP auth failed: {getattr(e, 'smtp_error', e)}")
    except smtplib.SMTPException as e:
        raise SMTPSendError(f"SMTP error: {e}")
    except (socket.gaierror, socket.timeout) as e:
        raise SMTPSendError(f"Network error to SMTP server: {e}")
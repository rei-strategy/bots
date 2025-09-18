# operators/realestate_ops.py
# -*- coding: utf-8 -*-

import os
import re
import time
import platform
from datetime import datetime
from typing import Optional, Tuple

from playwright.sync_api import sync_playwright, TimeoutError, Page

# --- Configuration -----------------------------------------------------------

# Credentials kept inline as requested
_PROPSTREAM_USER = os.getenv("PROPSTREAM_USER", "justin@padgeeks.com")
_PROPSTREAM_PASS = os.getenv("PROPSTREAM_PASS", "Takeover2025!")

# Headless + debug controls
_HEADLESS = os.getenv("PROPSTREAM_HEADLESS", "1").lower() not in ("0", "false", "no")
_DEBUG_SHOTS = os.getenv("PROPSTREAM_DEBUG", "0").lower() in ("1", "true", "yes")
_TRACE_LOG = os.getenv("PROPSTREAM_TRACE", "0").lower() in ("1", "true", "yes")

# Wait tuning
RESULT_RENDER_TIMEOUT_MS = int(os.getenv("PROPSTREAM_RESULT_TIMEOUT_MS", "20000"))
POLL_INTERVAL_MS = int(os.getenv("PROPSTREAM_POLL_INTERVAL_MS", "750"))
QUICK_RESULT_MS = int(os.getenv("PROPSTREAM_QUICK_RESULT_MS", "3000"))  # 3s quick wait after Enter

# --- Utilities ---------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
_UNIT_TOKEN_RE = re.compile(
    r",?\s*(Unit|Ste|Suite|Bldg|Building|Apt|#)\s+[A-Za-z0-9\-]+",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"[-]?\$?\s*[\d{1,3}(?:,\d{3})*]+(?:\.\d+)?")
_CLEAN_MONEY_RE = re.compile(r"[^\d\.\-]")

_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
_ENTITY_TOKENS = {"LLC", "L.L.C", "INC", "TRUST", "LP", "LLP", "CORP", "CO", "COMPANY"}

def _is_mac() -> bool:
    return platform.system().lower() == "darwin"

def _normalize_address(addr: str) -> str:
    addr = addr or ""
    addr = _UNIT_TOKEN_RE.sub("", addr)
    addr = _WHITESPACE_RE.sub(" ", addr).strip()
    return addr

def _parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    m = _MONEY_RE.search(text.replace("\xa0", " "))
    if not m:
        return None
    raw = _CLEAN_MONEY_RE.sub("", m.group(0))
    try:
        return float(raw)
    except Exception:
        return None

def _split_owner_hard(name: str) -> Tuple[str, str]:
    """
    Robust owner splitter:
    - Handles "FIRST LAST"
    - Handles "LAST, FIRST MIDDLE"
    - Handles "LAST FIRST MIDDLE" (all caps deed style like 'EVANS KATIE J')
    - Entities (LLC/Trust/Inc) => ("","")
    - Multiple owners separated by & or AND => take first owner
    """
    if not name:
        return "", ""
    n = name.strip()
    if not n:
        return "", ""

    # Take the first owner if multiple
    first_owner = re.split(r"\s+(?:&|AND)\s+", n, flags=re.IGNORECASE)[0].strip()

    # Entity check
    u_all = first_owner.upper()
    if any(tok in u_all for tok in _ENTITY_TOKENS):
        return "", ""

    # Comma form: "LAST, FIRST ..." -> last, first
    if "," in first_owner:
        parts = [p.strip() for p in first_owner.split(",", 1)]
        last = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        rest_tokens = [t for t in rest.split() if t.upper() not in _SUFFIXES]
        first = rest_tokens[0] if rest_tokens else ""
        return first, last

    tokens = first_owner.split()
    if len(tokens) == 1:
        # single token — treat as last name, leave first blank
        return "", tokens[0]

    # If string appears all-caps (or mostly), assume "LAST FIRST MI" order
    if first_owner == first_owner.upper():
        # EVANS KATIE J -> last=EVANS, first=KATIE
        last = tokens[0]
        rest_tokens = [t for t in tokens[1:] if t.upper() not in _SUFFIXES]
        first = rest_tokens[0] if rest_tokens else ""
        return first.title(), last.title()

    # Default: "First Last ..." -> first=tokens[0], last=" ".join(tokens[1:])
    first = tokens[0]
    rest_tokens = [t for t in tokens[1:] if t.upper() not in _SUFFIXES]
    last = " ".join(rest_tokens) if rest_tokens else ""
    return first, last

def _ts() -> str:
    return datetime.now().strftime("%H%M%S")

def _safe(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)[:120]

def _dump_html(obj, tag: str):
    if not _DEBUG_SHOTS:
        return
    safe = _safe(tag)
    html_path = f"debug_{safe}_{_ts()}.html"
    try:
        content = obj.content()
    except Exception:
        try:
            content = obj.evaluate("document.documentElement.outerHTML")
        except Exception:
            content = "<!-- content() failed -->"
    try:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"  [DEBUG] Saved {html_path}")
    except Exception as e:
        print(f"  [DEBUG] HTML dump failed: {e}")
    if isinstance(obj, Page):
        try:
            png = f"debug_{safe}_{_ts()}.png"
            obj.screenshot(path=png, full_page=True)
            print(f"  [DEBUG] Saved {png}")
        except Exception as e:
            print(f"  [DEBUG] Screenshot failed: {e}")

def _wait_idle(page: Page, timeout_ms: int = 6000):
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass

def _scroll_nudge(ctx):
    try:
        if isinstance(ctx, Page):
            ctx.keyboard.press("End"); ctx.wait_for_timeout(200)
            ctx.keyboard.press("Home"); ctx.wait_for_timeout(120)
        else:
            ctx.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.2)
            ctx.evaluate("window.scrollTo(0, 0)")
            time.sleep(0.12)
    except Exception:
        pass

# --- Selectors you provided (preferred) -------------------------------------

SEARCH_INPUT_PREFERRED = "input[id^='application_id__'][id$='_1'][placeholder^='Enter County, City, Zip Code']"
SEARCH_INPUT_FALLBACKS = [
    "input[placeholder='Enter County, City, Zip Code(s) or APN #']",
    "input[aria-label*='Search']",
    "input[type='search']",
    "input[placeholder*='County']",
]

# Equity title/value on the search result card
EQUITY_TITLE_CSS = "div.src-app-Search-Property-style__Ci7LI__title"
EQUITY_VALUE_CSS = "div.src-app-Search-Property-style__Er7lN__value"

# Result address link to click
RESULT_ADDR_LINK = "a.src-app-Search-Property-style__ubLK8__textOnly"

# Owner detail label/value
OWNER_LABEL_CSS = "div.src-components-GroupInfo-style__FpyDf__label"
OWNER_VALUE_WRAPPER = "div.src-components-GroupInfo-style__sbtoP__value div"

# --- Text-anchored fallbacks -------------------------------------------------

def _equity_title_xpath() -> str:
    # case-insensitive "EST. EQUITY"
    return ("xpath=//*[contains(translate(normalize-space(.),"
            "'abcdefghijklmnopqrstuvwxyz.','ABCDEFGHIJKLMNOPQRSTUVWXYZ '),'EST  EQUITY')]")

def _owner_label_xpath_variants():
    return [
        ("Owner 1 Name", "xpath=//*[self::div or self::span or self::h1 or self::h2 or self::h3]"
                         "[contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'OWNER 1 NAME')]"),
        ("Owner Name",   "xpath=//*[self::div or self::span or self::h1 or self::h2 or self::h3]"
                         "[contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'OWNER NAME')]"),
        ("Primary Owner","xpath=//*[self::div or self::span or self::h1 or self::h2 or self::h3]"
                         "[contains(translate(normalize-space(.),'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'PRIMARY OWNER')]"),
        ("Owner",        "xpath=//*[self::div or self::span or self::h1 or self::h2 or self::h3]"
                         "[normalize-space(translate(.,'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'))='OWNER']"),
    ]

# --- Presence & stability gating --------------------------------------------

def _presence_signals(ctx) -> bool:
    try:
        if "/search" in (ctx.url or "") or "/property/" in (ctx.url or ""):
            return True
    except Exception:
        pass
    try:
        if ctx.locator(_equity_title_xpath()).first.count():
            return True
    except Exception:
        pass
    try:
        if ctx.locator("a[href^='/search/']").first.count():
            return True
    except Exception:
        pass
    return False

def _render_stable(ctx, samples: int = 4, interval_ms: int = 250) -> bool:
    last_len = None
    stable = 0
    for _ in range(samples * 3):
        try:
            length = ctx.evaluate("document.body && document.body.innerText ? document.body.innerText.length : 0")
        except Exception:
            length = 0
        if last_len is not None and length == last_len and length > 200:
            stable += 1
            if stable >= samples:
                return True
        else:
            stable = 0
        last_len = length
        time.sleep(interval_ms / 1000.0)
    return False

def _wait_for_result_rendered(page: Page, tag: str, timeout_ms: int = RESULT_RENDER_TIMEOUT_MS) -> bool:
    start = time.time()
    deadline = start + (timeout_ms / 1000.0)
    while time.time() < deadline:
        _wait_idle(page, 800)
        if _presence_signals(page) and _render_stable(page, samples=4, interval_ms=250):
            if _TRACE_LOG:
                print(f"  [DBG] {tag}: result present+stable after {time.time()-start:0.1f}s")
            return True
        _scroll_nudge(page)
        page.wait_for_timeout(POLL_INTERVAL_MS)
    _dump_html(page, f"{tag}_timeout")
    return False

# --- Quick 3s wait after Enter ----------------------------------------------

def _quick_wait_for_search_signals(page: Page, timeout_ms: int = QUICK_RESULT_MS) -> bool:
    """
    After pressing Enter, PropStream needs up to ~3 seconds to render results.
    Wait up to `timeout_ms` for either:
      - at least one /search/<id> link, OR
      - the equity title/value classes to appear.
    """
    end = time.time() + (timeout_ms / 1000.0)
    while time.time() < end:
        try:
            if page.locator("a[href^='/search/']").first.count():
                return True
        except Exception:
            pass
        try:
            if page.locator(f"{EQUITY_TITLE_CSS}:has-text('EST. EQUITY')").first.count() and \
               page.locator(EQUITY_VALUE_CSS).first.count():
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False

# --- Banner handling ---------------------------------------------------------

def _dismiss_banners(page: Page):
    close_buttons = [
        "button:has-text('Close')",
        "text=Close >> xpath=ancestor::button[1]",
        "button[class*='Button'][class*='border-blue']:has-text('Close')",
    ]
    for sel in close_buttons:
        try:
            btn = page.locator(sel)
            if btn.count():
                btn.first.click(timeout=400)
                page.wait_for_timeout(80)
        except Exception:
            pass

# --- Extraction helpers ------------------------------------------------------

def _equity_from_exact_classes(page: Page) -> Optional[float]:
    """Use the exact PropStream classes for EST. EQUITY and its value."""
    try:
        title = page.locator(f"{EQUITY_TITLE_CSS}:has-text('EST. EQUITY')").first
        if not title.count():
            return None
        candidate = title.locator(
            f"xpath=ancestor::*[self::div or self::section][1]//{EQUITY_VALUE_CSS}"
        ).first
        if not candidate.count():
            candidate = title.locator("xpath=following::*[contains(text(), '$')][1]").first
        if candidate.count():
            return _parse_money((candidate.inner_text() or "").strip())
    except Exception:
        pass
    return None

def _equity_from_text(page: Page) -> Optional[float]:
    """Fallback: find 'EST. EQUITY' by text and pull the nearest $ value."""
    try:
        title = page.locator(_equity_title_xpath()).first
        if not title.count():
            return None
        money = title.locator("xpath=ancestor::*[self::div or self::section][1]//*[contains(text(), '$')]").first
        if not money.count():
            money = title.locator("xpath=following::*[contains(text(),'$')][1]").first
        if money.count():
            return _parse_money((money.inner_text() or "").strip())
    except Exception:
        pass
    return None

def _click_first_result(page: Page) -> bool:
    """Prefer clicking the textOnly address link; otherwise any /search/<id> link."""
    try:
        link = page.locator(RESULT_ADDR_LINK).first
        if link.count():
            before = page.url
            link.click()
            _wait_idle(page, 4000)
            if _TRACE_LOG:
                print(f"  [DBG] clicked address link: {before} -> {page.url}")
            return True
    except Exception:
        pass
    try:
        link2 = page.locator("a[href^='/search/']").first
        if link2.count():
            before = page.url
            link2.click()
            _wait_idle(page, 4000)
            if _TRACE_LOG:
                print(f"  [DBG] clicked generic search link: {before} -> {page.url}")
            return True
    except Exception:
        pass
    return False

def _owner_from_exact_classes(page: Page) -> str:
    """Use exact owner label/value classes on the detail page."""
    try:
        label = page.locator(f"{OWNER_LABEL_CSS}:has-text('Owner 1 Name')").first
        if not label.count():
            return ""
        val = page.locator(OWNER_VALUE_WRAPPER).first
        if val.count():
            return (val.inner_text() or "").strip()
    except Exception:
        pass
    return ""

def _owner_from_text(page: Page) -> str:
    """Fallback: search common owner label text and take adjacent value."""
    for _label, xp in _owner_label_xpath_variants():
        try:
            lab = page.locator(xp)
            if lab.count() == 0:
                continue
            node = lab.first
            sib_val = node.locator("xpath=following-sibling::*[1]").first
            if sib_val.count():
                txt = (sib_val.inner_text() or "").strip()
                if txt and not txt.lower().startswith("owner"):
                    return txt
            blk_val = node.locator(
                "xpath=ancestor::*[self::section or self::div][1]//*[self::div or self::span or self::a]"
            )
            n = blk_val.count()
            for i in range(min(n, 6)):
                t = (blk_val.nth(i).inner_text() or "").strip()
                if t and not t.lower().startswith("owner"):
                    return t
        except Exception:
            pass
    return ""

# --- Search UI helpers -------------------------------------------------------

def _get_visible_search_input(page: Page):
    preferred = page.locator(SEARCH_INPUT_PREFERRED).first
    try:
        if preferred.count() and preferred.is_visible():
            return preferred
    except Exception:
        pass
    for sel in SEARCH_INPUT_FALLBACKS:
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None

def _ensure_search_ready(page: Page) -> bool:
    try: page.keyboard.press("Escape")
    except Exception: pass
    try: page.keyboard.press("Home"); page.wait_for_timeout(60)
    except Exception: pass
    for sel in ("[data-testid='global-search']", "button[aria-label*='Search']",
                "button:has([data-icon='search'])", ".icon-search"):
        try:
            btn = page.locator(sel)
            if btn.count():
                btn.first.click(timeout=300)
                page.wait_for_timeout(80)
        except Exception:
            pass
    if _get_visible_search_input(page):
        return True
    try:
        page.goto("https://app.propstream.com/search", wait_until="domcontentloaded")
        _wait_idle(page, 3000)
    except Exception:
        pass
    return _get_visible_search_input(page) is not None

# --- Client -----------------------------------------------------------------

class _PropStreamClient:
    def __init__(self):
        pw = sync_playwright().start()
        self._pw = pw
        self.browser = pw.chromium.launch(
            headless=_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )
        self.page = self.browser.new_page()
        self.page.set_default_timeout(30000)
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self._login()

    def _login(self):
        print("  [RUN] Navigating to PropStream login…")
        self.page.goto("https://app.propstream.com/#/login", wait_until="domcontentloaded")
        try:
            user_sel = "input[type='email'], input[name='username']"
            pass_sel  = "input[type='password']"
            btn_sel   = "button[type='submit'], button:has-text('Sign In'), button:has-text('Log In')"
            self.page.wait_for_selector(user_sel, timeout=20000)
            self.page.fill(user_sel, _PROPSTREAM_USER)
            self.page.fill(pass_sel, _PROPSTREAM_PASS)
            self.page.click(btn_sel)
        except TimeoutError:
            pass
        _wait_idle(self.page, 3000)
        if _ensure_search_ready(self.page):
            print("  [RUN] Login succeeded – search input is visible.")
        else:
            print(f"  ⚠️ Login may have failed; current URL is {self.page.url}")
            _dump_html(self.page, "login_failed")

    # ---- Search flow (ENTER-ONLY + 3s grace) ----

    def _perform_search(self, address: str, city: str, zip_code: str) -> None:
        norm_addr = _normalize_address(address)
        loc_tail = f"{city or ''} {zip_code or ''}".strip()
        term = f"{norm_addr}, {loc_tail}" if loc_tail else norm_addr
        print(f"  [RUN] Intended search term: '{term}'")

        if not _ensure_search_ready(self.page):
            print("  ⚠️ No visible search input; forcing /search")
            try:
                self.page.goto("https://app.propstream.com/search", wait_until="domcontentloaded")
                _wait_idle(self.page, 3000)
            except Exception:
                pass
            if not _ensure_search_ready(self.page):
                print("  ⚠️ Still no visible search input.")
                _dump_html(self.page, "no_visible_search_input")
                return

        inp = _get_visible_search_input(self.page)
        if not inp:
            print("  ⚠️ Search input missing after ensure.")
            _dump_html(self.page, "search_input_missing_post_ensure")
            return

        try:
            inp.click(timeout=1000, force=True)
        except Exception:
            pass
        try:
            if _is_mac():
                self.page.keyboard.press("Meta+A")
            else:
                self.page.keyboard.press("Control+A")
            self.page.keyboard.press("Backspace")
            self.page.wait_for_timeout(30)
        except Exception:
            try: inp.fill("")
            except Exception: pass

        try:
            inp.type(term, delay=12)
        except Exception:
            try: inp.fill(term)
            except Exception:
                pass

        # ENTER-only; then blur; then quick 3s wait for results to show up
        try:
            self.page.keyboard.press("Enter")
            self.page.wait_for_timeout(200)
            self.page.keyboard.press("Enter")
            self.page.keyboard.press("Tab")
        except Exception:
            pass

        print("  [RUN] Submitted search by Enter (no autocomplete).")
        if not _quick_wait_for_search_signals(self.page, QUICK_RESULT_MS):
            if _TRACE_LOG:
                print("  [DBG] Quick wait expired without signals; continuing to full stability wait.")
        _wait_idle(self.page, 2500)
        _wait_for_result_rendered(self.page, tag="after_search", timeout_ms=RESULT_RENDER_TIMEOUT_MS)

    # ---- Public API ----

    def get_estimates(self, address: str, city: str, zip_code: str) -> Tuple[float, float, str, str]:
        attempts = [
            (address, city, zip_code),
            (address, city, ""),
            (address, "", ""),
        ]
        equity: Optional[float] = None
        owner = ""
        est_val = 0.0

        for idx, (a, c, z) in enumerate(attempts, 1):
            self._perform_search(a, c, z)

            # Try exact-class equity first, then text fallback
            eq_try = _equity_from_exact_classes(self.page)
            if eq_try is None:
                eq_try = _equity_from_text(self.page)

            if eq_try is not None:
                equity = eq_try
                print(f"  [DEBUG] Attempt {idx}: Equity parsed ${equity:,.0f}.")
            else:
                print(f"  [DEBUG] Attempt {idx}: Equity not found; dumping DOM.")
                _dump_html(self.page, f"equity_missing_attempt{idx}")

            # Owner: click into first result if possible; else try on current page
            clicked = _click_first_result(self.page)
            if clicked:
                _wait_for_result_rendered(self.page, tag=f"attempt_{idx}_after_click")
                _scroll_nudge(self.page)
                own = _owner_from_exact_classes(self.page)
                if not own:
                    own = _owner_from_text(self.page)
                if own:
                    owner = own
                    print(f"  [DEBUG] Attempt {idx}: Owner parsed '{owner}'.")
                else:
                    print(f"  [DEBUG] Attempt {idx}: Owner not visible; dumping DOM.")
                    _dump_html(self.page, f"owner_missing_attempt{idx}")
            else:
                # no click target; try to parse owner on current view (in case routing went directly to detail)
                own = _owner_from_exact_classes(self.page) or _owner_from_text(self.page)
                if own:
                    owner = own
                    print(f"  [DEBUG] Attempt {idx}: Owner parsed on current view '{owner}'.")
                else:
                    print(f"  [DEBUG] Attempt {idx}: No result link and no owner on current view.")

            if (equity is not None) or owner:
                break

        if equity is None:
            equity = 0.0
            _dump_html(self.page, "equity_missing_final")

        # === Name split fix: ensure First -> Column B, Last -> Column C ===
        first, last = _split_owner_hard(owner)
        print(f"  [RUN] Equity lookup returned ${equity:,.0f}, owner: {owner} -> first='{first}' last='{last}'")

        return float(equity or 0.0), float(est_val or 0.0), first, last

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw.stop()

# --- Public API (singleton) -------------------------------------------------

_client: Optional[_PropStreamClient] = None

def fetchPropStreamEstimates(address: str, city: str, zip_code: str):
    global _client
    if _client is None:
        _client = _PropStreamClient()
    return _client.get_estimates(address, city, zip_code)

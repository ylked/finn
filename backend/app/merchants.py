"""Merchant recognition: "Debit card payment // Migros M Dorigny" -> Migros.

Order of precedence for a transaction (unless the user locked its merchant):
1. patterns of the ledger's merchants (user-editable, learnt from corrections),
2. the built-in catalog (merchant_catalog.py), copied into the ledger on first hit,
3. the name proposed by the AI classifier, if any,
4. a cleaned-up counterparty name, for card / TWINT purchases only (not people, not transfers).
Within 1 and 2 the longest matching pattern wins.

Logos are fetched server-side from the merchant's own website (apple-touch-icon / favicon) and
cached in the database, so the browser never contacts third-party logo services.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import re
import socket
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .accounting import PAYMENT_KINDS
from .config import get_settings
from .merchant_catalog import CATALOG, PROCESSORS
from .models import Ledger, Merchant, Transaction

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ text helpers
def fold(s: str | None) -> str:
    """'Coop-2204 Laus.C. au C.' -> 'coop 2204 laus c au c' (lower, no accents, alnum words)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("œ", "oe").replace("æ", "ae").replace("ß", "ss")
    return " ".join(re.findall(r"[a-z0-9]+", s))


def _pattern_re(p: str) -> re.Pattern | None:
    prefix = p.endswith("*")
    p = fold(p.rstrip("*"))
    if not p:
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(p) + ("" if prefix else r"(?![a-z0-9])"))


def normalize_patterns(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for p in patterns:
        star = p.strip().endswith("*")
        f = fold(p.strip().rstrip("*"))
        if f:
            f += "*" if star else ""
            if f not in out:
                out.append(f)
    return out[:50]


_LEGAL = {"ag", "sa", "gmbh", "sarl", "sàrl", "ltd", "inc", "llc", "sas", "srl", "bv", "kg", "co", "plc", "s.a.", "s.a"}
# "DESMEULES, PATRICIA" (TWINT between people)
_PERSON = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+){0,2},\s*[^\W\d_]+(?:[ '\-][^\W\d_]+){0,2}$")


_PROC_RE = re.compile(r"(?i)(?<![a-z0-9])(" + "|".join(re.escape(p).replace(r"\ ", r"[\s.]") for p in PROCESSORS)
                      + r")\s*\*\s*")


def strip_processor(s: str) -> str:
    """'PAYPAL *NINTENDO' -> 'NINTENDO' (the processor is not the merchant)."""
    return _PROC_RE.sub("", s or "")


def raw_name(tx: Transaction) -> str:
    """Best raw payee text of a transaction."""
    if tx.counterparty and fold(tx.counterparty) not in PROCESSORS:
        return tx.counterparty
    d = tx.description or ""
    if "//" in d:
        d = d.split("//", 1)[1]
    return re.split(r"\s{2,}|\|", d.strip())[0]


_PARTICLES = {"de", "du", "des", "la", "le", "les", "et", "au", "aux", "d", "l", "of", "the", "and", "am", "im", "der", "die", "das"}


def _title(w: str, i: int) -> str:
    if w.lower() in _PARTICLES:
        return w.lower() if i > 0 else w.capitalize()
    if len(w) <= 3 and i == 0:
        return w  # TS PROD, BP, ...
    return "-".join(p.capitalize() for p in w.split("-"))


def clean_name(s: str) -> str | None:
    """'SUMUP *Heisse maronen' -> 'Heisse Maronen'; 'Ochsner Sport / 968' -> 'Ochsner Sport'."""
    s = strip_processor((s or "").strip()).strip()
    s = re.split(r"\s+/\s+|\s+\|\s+|,|\s{2,}", s)[0]
    words = s.split()
    # a store number ends the name: "Denner Discount 1725 Cha" -> "Denner Discount"
    cut = next((i for i, w in enumerate(words) if i > 0 and any(ch.isdigit() for ch in w)), None)
    if cut is not None:
        words = words[:cut]
    while words and (any(ch.isdigit() for ch in words[-1]) or words[-1].lower().strip(".") in _LEGAL or len(words[-1]) == 1):
        words.pop()
    while words and words[0].lower() in ("pos", "twint"):
        words.pop(0)
    s = " ".join(words).strip(" -.")
    if len(s) < 2 or not any(ch.isalpha() for ch in s):
        return None
    if s.isupper() and len(s) > 4:
        s = " ".join(_title(w, i) for i, w in enumerate(s.split()))
    return s[:120]


# ------------------------------------------------------------------ matching
@dataclass
class _Entry:
    regex: re.Pattern
    length: int
    merchant_id: int | None  # ledger merchant
    catalog_key: str | None  # or catalog entry not yet copied


def _best(entries: list[_Entry], text: str) -> _Entry | None:
    best = None
    for e in entries:
        if (best is None or e.length > best.length) and e.regex.search(text):
            best = e
    return best


def tx_text(tx: Transaction) -> str:
    return fold(strip_processor(f"{tx.counterparty or ''} {tx.description or ''}"))


_CATALOG_ENTRIES: list[_Entry] = []


def _catalog_entries() -> list[_Entry]:
    if not _CATALOG_ENTRIES:
        for key, (_, _, pats) in CATALOG.items():
            for p in pats:
                r = _pattern_re(p)
                if r:
                    _CATALOG_ENTRIES.append(_Entry(r, len(p.rstrip("*")), None, key))
    return _CATALOG_ENTRIES


class Matcher:
    """Merchant detection for one ledger. Build once and reuse for many transactions."""

    def __init__(self, db: Session, ledger: Ledger):
        self.db = db
        self.ledger = ledger
        self.ignored = set((ledger.settings or {}).get("merchant_catalog_ignored", []))
        self.owner_names = [fold(n) for n in (ledger.settings or {}).get("owner_names", []) if fold(n)]
        self.merchants: dict[int, Merchant] = {}
        self.by_catalog: dict[str, Merchant] = {}
        self.by_name: dict[str, Merchant] = {}
        self.entries: list[_Entry] = []
        for m in db.scalars(select(Merchant).where(Merchant.ledger_id == ledger.id)):
            self._add(m)
        self.catalog = [e for e in _catalog_entries() if e.catalog_key not in self.ignored]

    def _add(self, m: Merchant) -> None:
        self.merchants[m.id] = m
        if m.catalog_key:
            self.by_catalog[m.catalog_key] = m
        self.by_name.setdefault(fold(m.name), m)
        self.entries = [e for e in self.entries if e.merchant_id != m.id]
        for p in m.patterns or []:
            r = _pattern_re(p)
            if r:
                self.entries.append(_Entry(r, len(p.rstrip("*")), m.id, None))

    def refresh(self, m: Merchant) -> None:
        self._add(m)

    def _create(self, name: str, domain: str | None, patterns: list[str], catalog_key: str | None = None) -> Merchant:
        m = Merchant(ledger_id=self.ledger.id, name=name[:120], domain=domain, patterns=normalize_patterns(patterns),
                     catalog_key=catalog_key)
        self.db.add(m)
        self.db.flush()
        self._add(m)
        return m

    def from_catalog(self, key: str) -> Merchant:
        m = self.by_catalog.get(key)
        if m is None:
            name, domain, pats = CATALOG[key]
            m = self._create(name, domain, pats, key)
        return m

    def by_text(self, text: str) -> Merchant | None:
        """Pattern match: longest pattern among ledger merchants and catalog; on a tie the ledger wins."""
        e = _best(self.entries, text)
        # once copied into the ledger, the (editable) ledger copy replaces the catalog entry
        c = _best([x for x in self.catalog if x.catalog_key not in self.by_catalog], text)
        if c is not None and (e is None or c.length > e.length):
            return self.from_catalog(c.catalog_key)
        if e is not None:
            return self.merchants[e.merchant_id]
        return None

    def named(self, name: str, domain: str | None = None) -> Merchant:
        """Existing merchant with this name (or matching the catalog), else a new one."""
        key = fold(name)
        m = self.by_name.get(key) or self.by_text(key)
        if m is None:
            m = self._create(name, domain, [key])
        elif domain and not m.domain:
            m.domain = domain
        return m

    def _eligible_for_guess(self, tx: Transaction, text: str) -> bool:
        kinds = {ln.account.kind for ln in tx.lines}
        types = {ln.account.type for ln in tx.lines}
        if not kinds & PAYMENT_KINDS or "receivable" in kinds:
            return False
        if not ({"expense", "income"} & types or "suspense" in kinds):
            return False  # transfer between own accounts, card settlement, ...
        if any(o in text for o in self.owner_names):
            return False
        pay = sum((ln.amount for ln in tx.lines if ln.account.kind in PAYMENT_KINDS), start=0)
        if pay >= 0 and "refund" not in text and "rembours" not in text:
            return False  # money in: salary, people paying back, ... (refunds are kept)
        name = raw_name(tx)
        return not _PERSON.match(name.strip())

    def detect(self, tx: Transaction, *, ai_name: str | None = None, ai_domain: str | None = None,
               guess: bool = True) -> Merchant | None:
        """Set tx.merchant (unless locked). Returns the merchant or None."""
        if tx.merchant_locked or tx.source == "opening":
            return tx.merchant
        text = tx_text(tx)
        m = self.by_text(text)
        if m is None and ai_name and fold(ai_name) and self._eligible_for_guess(tx, text):
            m = self.named(ai_name.strip(), _clean_domain(ai_domain))
        if m is None and guess and self._eligible_for_guess(tx, text):
            name = clean_name(raw_name(tx))
            if name:
                m = self.named(name)
        if m is not None or guess:
            tx.merchant_id = m.id if m else None
            tx.merchant = m
        return m


def detect_all(db: Session, ledger: Ledger, *, only_missing: bool = True) -> int:
    """(Re)detect merchants on the whole ledger; returns how many transactions got one."""
    from sqlalchemy.orm import selectinload

    from .models import JournalLine

    m = Matcher(db, ledger)
    q = select(Transaction).where(Transaction.ledger_id == ledger.id, Transaction.merchant_locked.is_(False))
    if only_missing:
        q = q.where(Transaction.merchant_id.is_(None))
    n = 0
    for tx in db.scalars(q.options(selectinload(Transaction.lines).selectinload(JournalLine.account))):
        before = tx.merchant_id
        if m.detect(tx) is not None and tx.merchant_id != before:
            n += 1
    db.flush()
    return n


def apply_pattern(db: Session, ledger: Ledger, merchant: Merchant, pattern: str) -> int:
    """After a user correction: unlocked transactions matching ``pattern`` move to ``merchant``."""
    from sqlalchemy.orm import selectinload

    from .models import JournalLine

    r = _pattern_re(pattern)
    if r is None:
        return 0
    # a pattern belongs to one merchant: taking it away from others avoids ties on the next detection
    for other in db.scalars(select(Merchant).where(Merchant.ledger_id == ledger.id, Merchant.id != merchant.id)):
        if pattern in (other.patterns or []):
            other.patterns = [p for p in other.patterns if p != pattern]
    n = 0
    q = select(Transaction).where(Transaction.ledger_id == ledger.id, Transaction.merchant_locked.is_(False))
    for tx in db.scalars(q.options(selectinload(Transaction.lines).selectinload(JournalLine.account))):
        if tx.merchant_id != merchant.id and r.search(tx_text(tx)):
            tx.merchant_id = merchant.id
            n += 1
    return n


def learn_pattern(tx: Transaction) -> str | None:
    """Pattern describing this transaction's payee, e.g. 'migros m dorigny'."""
    name = clean_name(raw_name(tx))
    return fold(name) if name else None


# ------------------------------------------------------------------ logos
MAX_LOGO = 512 * 1024
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/x-icon",
               "image/vnd.microsoft.icon"}


def _clean_domain(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip().lower()
    if "://" in d:
        d = urlparse(d).hostname or ""
    d = d.split("/")[0].strip(".")
    if d.startswith("www."):
        d = d[4:]
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", d) and not re.fullmatch(r"[a-z0-9-]+\.[a-z0-9-]+", d):
        return None
    return d[:253]


clean_domain = _clean_domain


def _public_host(host: str) -> bool:
    """Refuse hosts resolving to private / loopback addresses (no SSRF into the local network)."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


class _IconParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        href = a.get("href")
        if not href or not ({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"} & set(rel)):
            return
        size = 0
        m = re.search(r"(\d+)x\d+", a.get("sizes", ""))
        if m:
            size = int(m.group(1))
        elif "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            size = 180
        elif href.lower().endswith(".svg") or a.get("type") == "image/svg+xml":
            size = 150
        else:
            size = 16
        self.icons.append((size, href))


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    for _ in range(4):  # follow redirects manually, checking every hop
        host = urlparse(url).hostname
        if not host or urlparse(url).scheme not in ("http", "https") or not _public_host(host):
            return None
        r = client.get(url)
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        return r
    return None


def _image(r: httpx.Response | None) -> tuple[bytes, str] | None:
    if r is None or r.status_code != 200 or len(r.content) > MAX_LOGO or len(r.content) < 64:
        return None
    mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
    body = r.content
    if mime not in IMAGE_TYPES:
        # some servers send icons as octet-stream
        if body[:4] == b"\x00\x00\x01\x00":
            mime = "image/x-icon"
        elif body[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        else:
            return None
    return body, mime


def fetch_logo(domain: str) -> tuple[bytes, str] | None:
    """Best icon of a website: largest declared icon, then /apple-touch-icon.png, then /favicon.ico."""
    base = f"https://{domain}/"
    try:
        with httpx.Client(timeout=6.0, headers={"User-Agent": _UA, "Accept": "text/html,image/*,*/*"},
                          follow_redirects=False) as client:
            candidates: list[tuple[int, str]] = []
            page = None
            for start in (base, f"https://www.{domain}/") if not domain.startswith("www.") else (base,):
                page = _get(client, start)
                if page is not None and page.status_code == 200 and "html" in page.headers.get("content-type", ""):
                    base = str(page.url)
                    break
            if page is not None and page.status_code == 200:
                p = _IconParser()
                try:
                    p.feed(page.text[:300_000])
                except Exception:  # noqa: BLE001 - malformed HTML
                    pass
                candidates = sorted(p.icons, key=lambda x: -x[0])
            good = [urljoin(base, h) for size, h in candidates if size >= 64 and not h.startswith("data:")]
            urls = good + [urljoin(base, "/apple-touch-icon.png")]
            # many shops block non-browser clients: optional icon service before falling back to tiny favicons
            if get_settings().merchant_logo_fallback == "duckduckgo":
                urls.append(f"https://icons.duckduckgo.com/ip3/{domain}.ico")
            urls += [urljoin(base, h) for size, h in candidates if size < 64 and not h.startswith("data:")]
            urls.append(urljoin(base, "/favicon.ico"))
            seen = set()
            for u in urls:
                if u in seen:
                    continue
                seen.add(u)
                img = _image(_get(client, u))
                if img:
                    return img
    except (httpx.HTTPError, ValueError) as e:
        log.info("logo fetch failed for %s: %s", domain, e)
    return None


LOGO_RETRY = dt.timedelta(days=7)


def ensure_logo(db: Session, m: Merchant) -> bool:
    """Fetch the logo if never tried (or failed a while ago). Returns True if a logo is available."""
    if m.logo_status in ("ok", "custom"):
        return True
    if not m.domain or not get_settings().merchant_logos:
        return False
    now = dt.datetime.now(dt.UTC)
    if m.logo_status == "failed" and m.logo_checked_at and now - m.logo_checked_at < LOGO_RETRY:
        return False
    img = fetch_logo(m.domain)
    m.logo_checked_at = now
    if img:
        m.logo, m.logo_mime = img
        m.logo_status = "ok"
    else:
        m.logo_status = "failed"
    db.commit()
    return img is not None


def logo_url(m: Merchant) -> str | None:
    if m.logo_status == "custom":
        v = int(m.logo_checked_at.timestamp()) if m.logo_checked_at else 0
    elif m.domain and m.logo_status != "failed":
        v = fold(m.domain).replace(" ", "")
    else:
        return None
    return f"/api/merchants/{m.id}/logo?v={v}"


def brief(m: Merchant | None) -> dict | None:
    return None if m is None else {"id": m.id, "name": m.name, "logo_url": logo_url(m)}

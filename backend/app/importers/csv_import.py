"""Generic CSV importer with a configurable column mapping (bank or credit card exports).

Sign convention of the produced entries (same as camt): + money in, - money out, seen
from the account holder. For a credit card, a purchase is money out (negative) and a
payment to the card is money in (positive). Most card exports list purchases as positive
amounts, hence the ``invert_sign`` option.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from .camt053 import ParsedEntry


class CsvMapping(BaseModel):
    delimiter: str = "auto"
    encoding: str = "auto"
    skip_rows: int = 0  # lines to skip before the header row
    has_header: bool = True
    date_column: str = ""
    date_format: str = "%d.%m.%Y"
    value_date_column: str | None = None
    description_columns: list[str] = Field(default_factory=list)
    counterparty_column: str | None = None
    amount_mode: str = "single"  # single | debit_credit
    amount_column: str | None = None
    debit_column: str | None = None  # money out column (positive numbers)
    credit_column: str | None = None  # money in column (positive numbers)
    invert_sign: bool = False
    decimal_separator: str = "."
    currency_column: str | None = None
    orig_amount_column: str | None = None
    orig_currency_column: str | None = None
    reference_column: str | None = None


class CsvError(ValueError):
    pass


def decode(content: bytes, encoding: str = "auto") -> str:
    if encoding != "auto":
        return content.decode(encoding)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise CsvError("Unable to decode file")


def _rows(text: str, m: CsvMapping) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()[m.skip_rows :]
    body = "\n".join(lines)
    delim = m.delimiter
    if delim == "auto":
        sample = "\n".join(lines[:20])
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
        except csv.Error:
            delim = ";" if sample.count(";") >= sample.count(",") else ","
    if delim == "\\t":
        delim = "\t"
    reader = list(csv.reader(io.StringIO(body), delimiter=delim))
    reader = [r for r in reader if any(c.strip() for c in r)]
    if not reader:
        return [], []
    if m.has_header:
        header = [h.strip() for h in reader[0]]
        return header, reader[1:]
    width = max(len(r) for r in reader)
    return [str(i) for i in range(width)], reader


def preview(content: bytes, m: CsvMapping, limit: int = 15) -> dict:
    text = decode(content, m.encoding)
    header, rows = _rows(text, m)
    return {"header": header, "rows": rows[:limit], "row_count": len(rows)}


def _col(header: list[str], row: list[str], name: str | None) -> str | None:
    if not name:
        return None
    try:
        idx = header.index(name)
    except ValueError:
        if name.isdigit():
            idx = int(name)
        else:
            raise CsvError(f"Column '{name}' not found") from None
    if idx >= len(row):
        return None
    v = row[idx].strip()
    return v or None


def parse_amount(v: str | None, decimal_sep: str = ".") -> Decimal | None:
    if v is None:
        return None
    s = v.strip().replace("−", "-").replace(" ", "").replace(" ", "")
    s = re.sub(r"[A-Za-z]{3}$|^[A-Za-z]{3}", "", s)  # currency code prefix / suffix
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("-"):
        neg, s = True, s[:-1]
    if decimal_sep == ",":
        s = s.replace(".", "").replace("'", "").replace(",", ".")
    else:
        s = s.replace(",", "").replace("'", "").replace("’", "")
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise CsvError(f"Invalid amount '{v}'") from e
    return -d if neg else d


def _parse_date(v: str | None, fmt: str) -> dt.date | None:
    if not v:
        return None
    v = v.strip()
    candidates = [v, v.split(" ")[0], v.split("T")[0]]
    for f in (fmt, "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d.%m.%y"):
        for c in candidates:
            try:
                return dt.datetime.strptime(c, f).date()
            except ValueError:
                continue
    try:
        return dt.date.fromisoformat(v[:10])
    except ValueError as e:
        raise CsvError(f"Invalid date '{v}' (expected {fmt})") from e


def parse(content: bytes, m: CsvMapping, default_currency: str) -> tuple[list[ParsedEntry], list[str]]:
    text = decode(content, m.encoding)
    header, rows = _rows(text, m)
    if not m.date_column:
        raise CsvError("Date column not configured")
    entries: list[ParsedEntry] = []
    errors: list[str] = []
    seen: dict[str, int] = {}
    for n, row in enumerate(rows, start=1 + m.skip_rows + (1 if m.has_header else 0)):
        try:
            date = _parse_date(_col(header, row, m.date_column), m.date_format)
            if date is None:
                continue  # summary / blank lines
            if m.amount_mode == "debit_credit":
                out_ = parse_amount(_col(header, row, m.debit_column), m.decimal_separator) or Decimal(0)
                in_ = parse_amount(_col(header, row, m.credit_column), m.decimal_separator) or Decimal(0)
                amount = abs(in_) - abs(out_)
            else:
                amount = parse_amount(_col(header, row, m.amount_column), m.decimal_separator)
                if amount is None:
                    continue
            if m.invert_sign:
                amount = -amount
            if amount == 0:
                continue
            desc = " ".join(x for c in m.description_columns if (x := _col(header, row, c)))
            cp = _col(header, row, m.counterparty_column)
            ccy = _col(header, row, m.currency_column) or default_currency
            orig_amt = parse_amount(_col(header, row, m.orig_amount_column), m.decimal_separator)
            orig_ccy = _col(header, row, m.orig_currency_column)
            if orig_amt is not None and orig_ccy and orig_ccy != ccy:
                orig_amt = -abs(orig_amt) if amount < 0 else abs(orig_amt)
            else:
                orig_amt = orig_ccy = None
            ref = _col(header, row, m.reference_column)
            base = ref or f"{date}|{amount}|{re.sub(r'\\s+', ' ', desc.lower())}"
            seen[base] = seen.get(base, 0) + 1
            key = "csv:" + hashlib.sha256(f"{base}#{seen[base]}".encode()).hexdigest()[:40]
            entries.append(
                ParsedEntry(
                    booking_date=date,
                    value_date=_parse_date(_col(header, row, m.value_date_column), m.date_format),
                    amount=amount.quantize(Decimal("0.01")),
                    currency=ccy[:3].upper(),
                    description=desc[:2000],
                    counterparty=cp,
                    bank_ref=ref,
                    orig_amount=orig_amt,
                    orig_currency=orig_ccy,
                    dedup_key=key,
                    raw={"row": row},
                )
            )
        except CsvError as e:
            errors.append(f"Line {n}: {e}")
    return entries, errors

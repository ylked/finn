"""Background worker for AI jobs.

State lives in the database (transactions.ai_status / receipts.status), so jobs survive
restarts. Rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED, which makes it safe to
run several app replicas.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, or_, select

from .ai.classify import classify_with_ai
from .ai.client import AIError
from .ai.receipts import extract, try_match
from .config import get_settings
from .db import SessionLocal
from .models import Receipt, Transaction

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def _guess_merchant(db, tx: Transaction) -> None:
    """No AI answer: fall back to the text heuristics for the merchant."""
    if tx.merchant_id is None and not tx.merchant_locked:
        from .merchants import Matcher
        from .models import Ledger

        Matcher(db, db.get(Ledger, tx.ledger_id)).detect(tx)


def _classify_one(tx_id: int) -> None:
    s = get_settings()
    with SessionLocal() as db:
        tx = db.scalar(
            select(Transaction)
            .where(Transaction.id == tx_id, Transaction.ai_status == "pending")
            .with_for_update(skip_locked=True)
        )
        if tx is None:
            return
        if not s.ai_enabled:
            tx.ai_status = "skipped"
            _guess_merchant(db, tx)
            db.commit()
            return
        if tx.state != "unclassified":
            tx.ai_status = "done"
            _guess_merchant(db, tx)
            db.commit()
            return
        tx.ai_attempts += 1
        try:
            classify_with_ai(db, tx)
            tx.ai_status = "done"
            _guess_merchant(db, tx)
        except AIError as e:
            log.warning("classification failed for tx %s: %s", tx_id, e)
            tx.ai_reason = f"Erreur IA / AI error: {e}"[:2000]
            if tx.ai_attempts >= MAX_ATTEMPTS:
                tx.ai_status = "failed"
                _guess_merchant(db, tx)
        except Exception:
            log.exception("unexpected classification error for tx %s", tx_id)
            db.rollback()
            with SessionLocal() as db2:
                t2 = db2.get(Transaction, tx_id)
                if t2:
                    t2.ai_attempts += 1
                    if t2.ai_attempts >= MAX_ATTEMPTS:
                        t2.ai_status = "failed"
                    db2.commit()
            return
        db.commit()


def _receipt_one(rid: int) -> None:
    s = get_settings()
    with SessionLocal() as db:
        r = db.scalar(
            select(Receipt).where(Receipt.id == rid, Receipt.status == "pending").with_for_update(skip_locked=True)
        )
        if r is None:
            return
        if not s.vision_enabled:
            r.status = "manual"
            db.commit()
            return
        r.attempts += 1
        try:
            r.extracted = extract(db, r)
            r.error = None
            r.status = "unmatched"
            db.flush()
            try_match(db, r)
        except AIError as e:
            log.warning("receipt %s extraction failed: %s", rid, e)
            r.error = str(e)[:2000]
            if r.attempts >= MAX_ATTEMPTS:
                r.status = "failed"
        except Exception as e:
            log.exception("receipt %s unexpected error", rid)
            db.rollback()
            r = db.get(Receipt, rid)
            r.attempts += 1
            r.error = str(e)[:2000]
            if r.attempts >= MAX_ATTEMPTS:
                r.status = "failed"
        db.commit()


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=max(1, get_settings().ai_max_concurrency))

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ai-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        interval = get_settings().ai_worker_interval
        while not self._stop.is_set():
            try:
                with SessionLocal() as db:
                    retry_before = func.now() - dt.timedelta(minutes=2)
                    tx_ids = db.scalars(
                        select(Transaction.id)
                        .where(
                            Transaction.ai_status == "pending",
                            or_(Transaction.ai_attempts == 0, Transaction.updated_at < retry_before),
                        )
                        .order_by(Transaction.id)
                        .limit(50)
                    ).all()
                    r_ids = db.scalars(
                        select(Receipt.id)
                        .where(Receipt.status == "pending", or_(Receipt.attempts == 0, Receipt.updated_at < retry_before))
                        .order_by(Receipt.id)
                        .limit(10)
                    ).all()
                futures = [self._pool.submit(_classify_one, i) for i in tx_ids]
                futures += [self._pool.submit(_receipt_one, i) for i in r_ids]
                for f in futures:
                    f.result()
                if len(tx_ids) == 50 or len(r_ids) == 10:
                    continue  # more work is waiting
            except Exception:
                log.exception("worker loop error")
            self._wake.wait(interval)
            self._wake.clear()


worker = Worker()

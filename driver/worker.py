import logging
import os
import sys
import threading
import time

from psycopg.types.json import Jsonb

from .db import connect
from .image_worker import next_task, recover, registry


def heartbeat(db, state, model=None, error=None):
    db.execute(
        """INSERT INTO worker_status(singleton,state,model,updated,error) VALUES(true,%s,%s,now(),%s)
                  ON CONFLICT(singleton) DO UPDATE SET state=excluded.state,model=excluded.model,
                  updated=excluded.updated,error=excluded.error""",
        (state, Jsonb(model) if model else None, error),
    )


def run_worker():
    logging.basicConfig(level=logging.INFO)
    stopping = threading.Event()
    if os.getenv("DRIVER_PARENT_PID"):

        def wait_parent():
            sys.stdin.buffer.read()
            stopping.set()

        threading.Thread(target=wait_parent, daemon=True).start()
    with connect() as db:
        db.autocommit = True
        # This dedicated connection owns the only local worker slot until disconnect.
        # On a fast restart the previous worker may still be finishing its current batch.
        lock_deadline = time.monotonic() + 30
        while not db.execute("SELECT pg_try_advisory_lock(285011740) AS ok").fetchone()["ok"]:
            if stopping.wait(0.4):
                return
            if time.monotonic() > lock_deadline:
                raise RuntimeError("Another Driver Rating worker already owns this database")
        heartbeat(db, "starting")
        predictors, models = registry()
        recover(db)

        def pulse():
            heartbeat(db, "ready", models)

        last_clean = last_beat = 0.0
        while not stopping.is_set():
            now = time.monotonic()
            if now - last_beat > 3:
                pulse()
                last_beat = now
            if now - last_clean > 60:
                db.execute("DELETE FROM image_comparisons WHERE NOT pinned AND expires<=now()")
                db.execute("DELETE FROM analyses WHERE expires<=now()")
                last_clean = now
            if not next_task(db, predictors, models, pulse):
                stopping.wait(0.35)

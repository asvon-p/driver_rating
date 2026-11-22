import logging
import os
import sys
import threading
import time

from psycopg.types.json import Jsonb

from local_runtime import STATE

from .db import connect
from .scoring import POLICY_ID
from .video import analyze

log = logging.getLogger(__name__)
UPLOADS = STATE / "uploads"


def heartbeat(db, state, model=None, error=None):
    db.execute(
        """INSERT INTO worker_status(singleton,state,model,updated,error) VALUES(true,%s,%s,now(),%s)
                  ON CONFLICT(singleton) DO UPDATE SET state=excluded.state,model=excluded.model,
                  updated=excluded.updated,error=excluded.error""",
        (state, Jsonb(model) if model else None, error),
    )


def claim(db):
    return db.execute("""UPDATE analyses SET state='running',attempts=attempts+1
        WHERE id=(SELECT id FROM analyses WHERE state='queued' ORDER BY created
                  FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *""").fetchone()


def clean(db):
    retired = db.execute("""UPDATE analyses SET state='failed',error='원본 영상 보관 기간이 지났습니다.'
                            WHERE state IN ('running','queued') AND created<now()-interval '24 hours'
                            RETURNING id""").fetchall()
    for row in retired:
        (UPLOADS / f"{row['id']}.mp4").unlink(missing_ok=True)
    db.execute("DELETE FROM analyses WHERE expires < now()")
    for path in UPLOADS.glob("*"):
        if path.suffix in (".mp4", ".part") and path.stat().st_mtime < time.time() - 86400:
            path.unlink(missing_ok=True)


def process(db, job, predictor, model):
    path = UPLOADS / f"{job['id']}.mp4"

    def progress(ms):
        db.execute("UPDATE analyses SET processed_ms=%s WHERE id=%s", (ms, job["id"]))
        heartbeat(db, "ready", model)

    try:
        if job["model"]["version"] != model["version"] or job["policy"] != POLICY_ID:
            raise ValueError("분석 설정이 변경됐습니다. 영상을 다시 올려 주세요.")
        result, segments = analyze(path, job["duration_ms"], predictor, progress)
        with db.transaction():
            with db.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO segments VALUES (%s,%s,%s,%s,%s)",
                    [
                        (job["id"], s["start_ms"], s["end_ms"], s["label"], s["reason"])
                        for s in segments
                    ],
                )
            final_state = "insufficient" if result["score"] is None else "completed"
            db.execute(
                "UPDATE analyses SET state=%s,result=%s,processed_ms=duration_ms WHERE id=%s",
                (final_state, Jsonb(result), job["id"]),
            )
    except Exception as exc:
        log.exception("Analysis %s failed", job["id"])
        message = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith("분석 설정")
            else "영상 분석에 실패했습니다. 지원하는 H.264 MP4인지 확인해 주세요."
        )
        db.execute("UPDATE analyses SET state='failed',error=%s WHERE id=%s", (message, job["id"]))
    path.unlink(missing_ok=True)


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
        try:
            from .predictor import load_model

            predictor, model = load_model()
        except Exception:
            heartbeat(
                db,
                "error",
                error="모델 준비에 실패했습니다. 체크포인트 설정과 .runtime/logs/worker.log를 확인해 주세요.",
            )
            raise
        failed = db.execute("""UPDATE analyses SET state='failed',error='워커가 두 차례 중단되었습니다.'
                             WHERE state='running' AND attempts>=2 RETURNING id""").fetchall()
        for job in failed:
            (UPLOADS / f"{job['id']}.mp4").unlink(missing_ok=True)
        db.execute("UPDATE analyses SET state='queued',processed_ms=0 WHERE state='running'")
        # A prior process may have stopped after committing a result but before deleting its upload.
        for job in db.execute(
            "SELECT id FROM analyses WHERE state IN ('failed','completed','insufficient')"
        ):
            (UPLOADS / f"{job['id']}.mp4").unlink(missing_ok=True)
        last_clean = last_beat = 0.0
        while not stopping.is_set():
            now = time.monotonic()
            if now - last_beat > 3:
                heartbeat(db, "ready", model)
                last_beat = now
            if now - last_clean > 60:
                clean(db)
                last_clean = now
            job = claim(db)
            if job:
                process(db, job, predictor, model)
            else:
                stopping.wait(0.4)

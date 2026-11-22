import hashlib
import os
import secrets
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.json import Jsonb

from local_runtime import ROOT, STATE

from .db import connect, worker_status
from .scoring import POLICY_ID, REASONS
from .video import MAX_BYTES, VideoError, inspect_video

def load_upload_code(path):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    if not path.exists():
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
        with path.open("x", encoding="ascii") as handle:
            handle.write(code + "\n")
    code = path.read_bytes().strip()
    if not (16 <= len(code) <= 64 and all(33 <= char <= 126 for char in code)):
        raise RuntimeError("Invalid .runtime/demo-access-code. Stop the server and rotate the code.")
    return code


@asynccontextmanager
async def lifespan(app):
    app.state.upload_code = load_upload_code(STATE / "demo-access-code")
    yield


app = FastAPI(title="Driver Rating", lifespan=lifespan)
STATIC = ROOT / "web"
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")


class UploadGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        limit = MAX_BYTES + 128 * 1024  # multipart overhead; the file itself is checked separately
        headers = dict(scope["headers"])
        supplied_code = headers.get(b"x-demo-code", b"")
        if not secrets.compare_digest(supplied_code, scope["app"].state.upload_code):
            return await JSONResponse(
                {"detail": "시연용 접근 코드를 확인해 주세요."}, status_code=403
            )(scope, receive, send)
        if int(headers.get(b"content-length", b"0")) > limit:
            return await JSONResponse({"detail": "100 MB 이하 영상을 선택해 주세요."}, 413)(
                scope, receive, send
            )
        count = 0

        async def limited():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > limit:
                raise HTTPException(413, "100 MB 이하 영상을 선택해 주세요.")
            return message

        await self.app(scope, limited, send)


app.add_middleware(UploadGuard)


def session(request, response):
    token = request.cookies.get("driver_session")
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            "driver_session", token, httponly=True, samesite="strict", max_age=7 * 86400
        )
    return hashlib.sha256(token.encode()).hexdigest()


@app.get("/", include_in_schema=False)
def home(request: Request):
    response = FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})
    session(request, response)
    return response


@app.get("/health")
def health():
    return worker_status()


@app.post("/analyses", status_code=202)
def create(request: Request, response: Response, video: UploadFile):
    ident = uuid4()
    part = STATE / "uploads" / f"{ident}.part"
    path = part.with_suffix(".mp4")
    registered = False
    try:
        ready = worker_status()
        if ready["state"] != "ready":
            raise HTTPException(503, ready["message"])
        size = 0
        with part.open("xb") as output:
            while block := video.file.read(1024 * 1024):
                size += len(block)
                if size > MAX_BYTES:
                    raise HTTPException(413, "100 MB 이하 영상을 선택해 주세요.")
                output.write(block)
        duration, _ = inspect_video(part)
        os.replace(part, path)
        with connect() as db:
            db.execute(
                """INSERT INTO analyses(id,owner,state,duration_ms,model,policy)
                          VALUES (%s,%s,'queued',%s,%s,%s)""",
                (ident, session(request, response), duration, Jsonb(ready["model"]), POLICY_ID),
            )
        registered = True
        return {"id": ident, "status_url": f"/analyses/{ident}"}
    except VideoError as exc:
        raise HTTPException(exc.code, str(exc)) from exc
    finally:
        video.file.close()
        part.unlink(missing_ok=True)
        if not registered:
            path.unlink(missing_ok=True)


def owned_job(db, ident, request):
    token = request.cookies.get("driver_session", "")
    owner = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(
        """SELECT id,state,duration_ms,processed_ms,model,policy,result,error FROM analyses
                      WHERE id=%s AND owner=%s AND expires>now()""",
        (ident, owner),
    ).fetchone()
    if not row:
        raise HTTPException(404, "분석 결과를 찾을 수 없습니다.")
    return row


@app.get("/analyses/{ident}")
def status(ident: UUID, request: Request):
    with connect() as db:
        row = owned_job(db, ident, request)
        row.pop("result")
        return row


@app.get("/analyses/{ident}/result")
def result(ident: UUID, request: Request):
    with connect() as db:
        row = owned_job(db, ident, request)
        if row["state"] not in ("completed", "insufficient"):
            raise HTTPException(409, row["error"] or "분석이 아직 완료되지 않았습니다.")
        timeline = db.execute(
            """SELECT start_ms,end_ms,label,reason FROM segments
                               WHERE analysis_id=%s ORDER BY start_ms""",
            (ident,),
        ).fetchall()
        return dict(
            **row["result"],
            state=row["state"],
            model=row["model"],
            policy=row["policy"],
            segments=timeline,
            reason_names=REASONS,
        )

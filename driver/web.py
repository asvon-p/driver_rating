import hashlib
import hmac
import io
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError
from psycopg.types.json import Jsonb
from starlette.formparsers import MultiPartException

from local_runtime import ROOT, STATE
from .comparison import ROLES, RULES
from .db import connect, worker_status

MAX_FILES = 24
MAX_FILE = 10 * 1024 * 1024
MAX_BODY = 48 * 1024 * 1024
CAM_METHOD = "layer4-ensemble-v1"
BASE = "/api/image-comparisons"
VISIBLE = "(pinned OR expires>now())"
COOKIE = "driver_admin"


def load_upload_code(path):
    if not path.exists():
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4))
        with path.open("x", encoding="ascii") as handle:
            handle.write(code + "\n")
    code = path.read_bytes().strip()
    if not (16 <= len(code) <= 64 and all(33 <= c <= 126 for c in code)):
        raise RuntimeError("Invalid .runtime/demo-access-code")
    return code


@asynccontextmanager
async def lifespan(app):
    app.state.secret = load_upload_code(STATE / "demo-access-code")
    yield


app = FastAPI(title="Driver Rating", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")


def signature(secret, value):
    return hmac.new(secret, value.encode("ascii"), hashlib.sha256).hexdigest()


def authenticated(request):
    token = request.cookies.get(COOKIE, "")
    try:
        expiry, nonce, digest = token.split(".")
        return (len(token) < 130 and int(expiry) >= time.time()
                and hmac.compare_digest(digest, signature(app.state.secret, expiry + "." + nonce)))
    except (ValueError, UnicodeError, TypeError):
        return False


class AdminGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        if scope["path"] != "/api/admin/session" and not authenticated(request):
            return await JSONResponse({"detail": "관리자 로그인이 필요합니다."}, 403)(scope, receive, send)
        if request.headers.get("sec-fetch-site") == "cross-site" and request.method != "GET":
            return await JSONResponse({"detail": "동일 사이트에서 요청해 주세요."}, 403)(scope, receive, send)
        size = request.headers.get("content-length", "0")
        if not size.isdigit() or int(size) > MAX_BODY:
            return await JSONResponse({"detail": "묶음은 전송 크기 48 MiB 이하여야 합니다."}, 413)(scope, receive, send)
        consumed = 0

        async def limited():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > MAX_BODY:
                raise MultiPartException("묶음의 전송 한도 48 MiB를 초과했습니다.")
            return message

        async def private(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, limited, private)


app.add_middleware(AdminGuard)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    status = worker_status()
    return {"state": status["state"]}


@app.get("/api/admin/session")
def session(request: Request):
    return {"authenticated": authenticated(request)}


@app.post("/api/admin/session")
def login(request: Request, response: Response):
    if not secrets.compare_digest(request.headers.get("x-demo-code", "").encode(), app.state.secret):
        raise HTTPException(403, "접근 코드를 확인해 주세요.")
    lifetime = 12 * 3600
    value = f"{int(time.time()) + lifetime}.{secrets.token_hex(12)}"
    response.set_cookie(COOKIE, value + "." + signature(app.state.secret, value), max_age=lifetime,
                        httponly=True, samesite="strict", secure=os.getenv("DRIVER_PUBLIC_SESSION") == "1"
                        or request.url.scheme == "https")
    return {"authenticated": True}


@app.delete("/api/admin/session")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"authenticated": False}


@app.get("/api/admin/status")
def admin_status():
    return worker_status()


class PhotoMeta(BaseModel):
    id: UUID
    name: str = Field(min_length=1, max_length=240)
    view: Literal["미지정", "기존 옆 시점", "새 측면", "정면"] = "미지정"
    source: str = Field(default="", max_length=500)
    truth: int | None = Field(default=None, ge=0, le=9)
    exclude: Literal["", "운전자 없음", "10종 외 행동"] = ""


def encode(image, kind, **kwargs):
    out = io.BytesIO()
    image.save(out, format=kind, **kwargs)
    return out.getvalue()


def read_image(upload, index):
    stream = upload.file
    if stream.seek(0, 2) > MAX_FILE:
        raise HTTPException(413, f"{index}번 사진이 10 MiB를 초과합니다.")
    stream.seek(0)
    try:
        with Image.open(stream) as source:
            if source.format not in ("JPEG", "PNG") or getattr(source, "n_frames", 1) != 1:
                raise ValueError
            if source.width * source.height > 20_000_000:
                raise HTTPException(413, f"{index}번 사진이 2,000만 픽셀을 초과합니다.")
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            rgb.info.clear()
            pixels = encode(rgb, "PNG", compress_level=2)
            rgb.thumbnail((320, 240))
            return pixels, encode(rgb, "JPEG", quality=82)
    except (OSError, ValueError, Image.DecompressionBombError, UnidentifiedImageError) as exc:
        raise HTTPException(422, f"{index}번 사진을 읽을 수 없습니다. JPG·PNG 파일을 확인하세요.") from exc


@app.post(BASE, status_code=202)
def create(files: list[UploadFile] = File(), order: str = Form(), title: str = Form(max_length=120)):
    try:
        raw = json.loads(order)
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FILES:
            raise ValueError
        items = [PhotoMeta.model_validate(x) for x in raw]
    except (ValueError, TypeError, ValidationError) as exc:
        raise HTTPException(422, "1~24장의 사진과 올바른 순서가 필요합니다.") from exc
    uploads = {f.filename: f for f in files}
    ids = {str(m.id) for m in items}
    if len(files) != len(items) or len(ids) != len(items) or ids != set(uploads):
        raise HTTPException(422, "사진과 순서 목록이 일치하지 않습니다.")
    ready = worker_status()
    if ready["state"] != "ready" or not any("version" in m for m in ready["model"].values()):
        raise HTTPException(503, "사용 가능한 모델이 없습니다. 저장 결과는 열 수 있습니다.")
    ident = uuid4()
    rules = dict(RULES, cam_method=CAM_METHOD)
    with connect() as db:
        db.execute("""INSERT INTO image_comparisons(id,title,models,rules,photo_count) VALUES (%s,%s,%s,%s,%s)""",
                   (ident, title.strip() or "사진 묶음", Jsonb(ready["model"]), Jsonb(rules), len(items)))
        for pos, item in enumerate(items, 1):
            pixels, thumbnail = read_image(uploads[str(item.id)], pos)
            db.execute("""INSERT INTO comparison_photos(id,comparison_id,position,name,metadata,pixels,thumbnail)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(), ident, pos, item.name, Jsonb(item.model_dump(exclude={"id", "name"})), pixels, thumbnail))
    return {"id": ident}


@app.get(BASE)
def history(offset: int = Query(default=0, ge=0)):
    with connect() as db:
        return db.execute(f"""SELECT id,title,created,pinned,state,photo_count,summary,
            (SELECT id FROM comparison_photos WHERE comparison_id=c.id ORDER BY position LIMIT 1) AS cover
            FROM image_comparisons c WHERE {VISIBLE} ORDER BY pinned DESC,created DESC,id LIMIT 25 OFFSET %s""",
            (offset,)).fetchall()


def require_case(db, ident):
    row = db.execute(f"SELECT * FROM image_comparisons WHERE id=%s AND {VISIBLE}", (ident,)).fetchone()
    if not row:
        raise HTTPException(404, "묶음을 찾을 수 없습니다.")
    return row


@app.get(BASE + "/{ident}")
def detail(ident: UUID):
    with connect() as db:
        row = require_case(db, ident)
        row["photos"] = db.execute("""SELECT id,position,name,metadata,results FROM comparison_photos
            WHERE comparison_id=%s ORDER BY position""", (ident,)).fetchall()
        return row


@app.put(BASE + "/{ident}/pin")
def pin(ident: UUID, pinned: bool):
    with connect() as db:
        row = db.execute(f"""UPDATE image_comparisons SET pinned=%s,expires=CASE WHEN pinned AND NOT %s
            THEN now()+interval '7 days' ELSE expires END WHERE id=%s AND {VISIBLE} RETURNING pinned""",
            (pinned, pinned, ident)).fetchone()
        if not row:
            raise HTTPException(404, "묶음을 찾을 수 없습니다.")
        return row


@app.delete(BASE + "/{ident}")
def delete_case(ident: UUID):
    with connect() as db:
        require_case(db, ident)
        db.execute("DELETE FROM image_comparisons WHERE id=%s", (ident,))
    return {"deleted": True}


@app.get(BASE + "/{ident}/photos/{photo}")
def photo_image(ident: UUID, photo: UUID, thumb: bool = False):
    col = "thumbnail" if thumb else "pixels"
    with connect() as db:
        row = db.execute(f"""SELECT {col} AS data FROM comparison_photos p JOIN image_comparisons c
            ON c.id=p.comparison_id WHERE c.id=%s AND p.id=%s AND {VISIBLE}""", (ident, photo)).fetchone()
        if not row:
            raise HTTPException(404, "사진을 찾을 수 없습니다.")
        return Response(row["data"], media_type="image/jpeg" if thumb else "image/png")


@app.api_route(BASE + "/{ident}/photos/{photo}/cam/{role}", methods=["GET", "POST"])
def cam(ident: UUID, photo: UUID, role: Literal["A", "B"], request: Request):
    with connect() as db:
        row = db.execute(f"""SELECT p.results,c.rules,c.models FROM comparison_photos p JOIN image_comparisons c
            ON c.id=p.comparison_id WHERE c.id=%s AND p.id=%s AND {VISIBLE}""", (ident, photo)).fetchone()
        if not row or "label" not in ((row["results"] or {}).get(role) or {}):
            raise HTTPException(409, "설명할 사진별 예측이 없습니다.")
        method = row["rules"]["cam_method"]
        params = (photo, role, method)
        record = db.execute("SELECT state,error,image FROM comparison_heatmaps WHERE photo_id=%s AND role=%s AND method=%s",
                            params).fetchone()
        if record and record["state"] == "ready":
            return Response(record["image"], media_type="image/jpeg")
        if request.method == "POST":
            status = worker_status()
            if (status["state"] != "ready" or status["model"][role].get("version") != row["models"][role].get("version")
                    or method != CAM_METHOD):
                raise HTTPException(409, "저장된 히트맵이 없습니다. 당시 모델과 설명 버전이 필요합니다.")
            db.execute("""INSERT INTO comparison_heatmaps(photo_id,role,method) VALUES (%s,%s,%s)
                ON CONFLICT(photo_id,role,method) DO UPDATE SET state='queued',attempts=0,error=NULL
                WHERE comparison_heatmaps.state='failed'""", params)
            return JSONResponse({"state": "queued"}, 202)
        if record and record["state"] == "failed":
            raise HTTPException(409, record["error"])
        return JSONResponse({"state": record["state"] if record else "absent"}, 202)

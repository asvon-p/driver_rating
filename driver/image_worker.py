"""A single worker owns inference and Grad-CAM hooks for both configured models."""
import io
import logging
import os

from PIL import Image
from psycopg.types.json import Jsonb

from local_runtime import ROOT
from .comparison import ROLES, compare, decision
from .predictor import checked_predictions, load_model
from .video import assess_image

log = logging.getLogger(__name__)


def registry():
    predictors, models = {}, {}
    for role in ROLES:
        config = os.getenv(f"DRIVER_MODEL_{role}_CONFIG", ROOT / "models" / f"comparison-{role.lower()}.json")
        try:
            predictors[role], models[role] = load_model(config)
        except Exception:
            log.exception("Unable to load role %s", role)
            models[role] = {"name": f"모델 {role}", "error": "모델 준비 중 · 설정과 체크포인트 확인 필요"}
    return predictors, models


def process_comparison(db, job, predictors, models, pulse):
    photos = db.execute("SELECT id,metadata,results FROM comparison_photos WHERE comparison_id=%s ORDER BY position",
                        (job["id"],)).fetchall()
    for index, photo in enumerate(photos, 1):
        if photo["results"] is not None:
            continue
        raw = db.execute("SELECT pixels FROM comparison_photos WHERE id=%s", (photo["id"],)).fetchone()
        if raw is None:
            return  # An administrator deleted this case.
        with Image.open(io.BytesIO(raw["pixels"])) as decoded:
            image = decoded.convert("RGB")
        reason = photo["metadata"].get("exclude")
        if not reason and assess_image(image, job["rules"]["quality"])[1]:
            reason = "어둡거나 불분명한 사진"
        results = {}
        for role in ROLES:
            recorded = job["models"][role]
            if "version" not in recorded:
                results[role] = {"score": None, "reason": recorded["error"]}
            elif models[role].get("version") != recorded["version"]:
                results[role] = {"score": None, "reason": "분석을 시작할 때의 모델 버전을 사용할 수 없습니다."}
            elif reason:
                results[role] = {"score": None, "reason": reason}
            else:
                try:
                    pred = checked_predictions(predictors[role], [image])[0]
                    results[role] = decision(pred, job["rules"])
                except Exception:
                    log.exception("Inference failed for %s %s", photo["id"], role)
                    results[role] = {"score": None, "reason": "모델 추론 오류"}
        photo["results"] = results
        with db.transaction():
            db.execute("UPDATE comparison_photos SET results=%s WHERE id=%s", (Jsonb(results), photo["id"]))
            db.execute("UPDATE image_comparisons SET processed=%s WHERE id=%s", (index, job["id"]))
        pulse()
    summary = compare(photos, job["rules"])
    db.execute("UPDATE image_comparisons SET state='completed',summary=%s WHERE id=%s",
               (Jsonb(summary), job["id"]))


def process_cam(db, task, predictors, models):
    params = (task["photo_id"], task["role"], task["method"])
    row = db.execute("""SELECT p.pixels,p.results,c.models FROM comparison_photos p
                       JOIN image_comparisons c ON c.id=p.comparison_id WHERE p.id=%s""", (params[0],)).fetchone()
    if row is None:
        return
    role = task["role"]
    try:
        if models[role].get("version") != row["models"][role].get("version") or role not in predictors:
            raise ValueError("분석 당시 모델 버전이 필요합니다.")
        with Image.open(io.BytesIO(row["pixels"])) as im:
            overlay = predictors[role].heatmap(im.convert("RGB"), row["results"][role]["label"])
        out = io.BytesIO()
        overlay.save(out, format="JPEG", quality=90)
        db.execute("""UPDATE comparison_heatmaps SET state='ready',image=%s,error=NULL
                      WHERE photo_id=%s AND role=%s AND method=%s""", (out.getvalue(), *params))
    except Exception as exc:
        log.exception("Grad-CAM failed for %s %s", *params[:2])
        error = str(exc) if isinstance(exc, ValueError) else "Grad-CAM 생성에 실패했습니다."
        db.execute("""UPDATE comparison_heatmaps SET state='failed',error=%s
                      WHERE photo_id=%s AND role=%s AND method=%s""", (error, *params))


def recover(db):
    for table in ("image_comparisons", "comparison_heatmaps"):
        db.execute(f"UPDATE {table} SET state='failed',error='워커가 반복 중단되었습니다.' WHERE state='running' AND attempts>=2")
        db.execute(f"UPDATE {table} SET state='queued' WHERE state='running'")


def next_task(db, predictors, models, pulse):
    task = db.execute("""UPDATE comparison_heatmaps SET state='running',attempts=attempts+1
        WHERE (photo_id,role,method)=(SELECT photo_id,role,method FROM comparison_heatmaps WHERE state='queued'
        ORDER BY photo_id,role FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING photo_id,role,method""").fetchone()
    if task:
        process_cam(db, task, predictors, models)
        return True
    job = db.execute("""UPDATE image_comparisons SET state='running',attempts=attempts+1
        WHERE id=(SELECT id FROM image_comparisons WHERE state='queued' ORDER BY created
        FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id,models,rules""").fetchone()
    if not job:
        return False
    try:
        process_comparison(db, job, predictors, models, pulse)
    except Exception:
        log.exception("Comparison %s failed", job["id"])
        db.execute("UPDATE image_comparisons SET state='failed',error='사진 묶음 처리 실패' WHERE id=%s", (job["id"],))
    return True

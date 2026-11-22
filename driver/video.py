from pathlib import Path

import av
import numpy as np
from PIL import Image

from .scoring import POLICY, append_segment, score

MAX_BYTES = 100_000_000
MAX_MS = 180_000


class VideoError(ValueError):
    def __init__(self, message, code=422):
        super().__init__(message)
        self.code = code


def inspect_video(path: Path):
    with path.open("rb") as file:
        head = file.read(12)
    if head[4:8] != b"ftyp" or head[8:12] == b"qt  ":
        raise VideoError("MP4 컨테이너의 영상을 선택해 주세요.", 415)
    try:
        with open_video(path) as video:
            stream = next(
                (
                    s
                    for s in video.streams.video
                    if not (s.disposition & av.stream.Disposition.attached_pic)
                ),
                None,
            )
            if stream is None or stream.codec_context.name != "h264":
                raise VideoError("H.264로 인코딩된 MP4가 필요합니다.", 415)
            check_dimensions(stream.width, stream.height)
            if not stream.duration:
                raise VideoError("영상 트랙의 재생 시간을 읽을 수 없습니다.")
            duration_ms = round(stream.duration * stream.time_base * 1000)
            if not 0 < duration_ms <= MAX_MS:
                raise VideoError("영상은 3분 이내여야 합니다.")
            return duration_ms, stream.index
    except av.error.FFmpegError as exc:
        raise VideoError("영상 파일이 손상되어 열 수 없습니다.") from exc


def check_dimensions(width, height):
    if min(width, height) <= 0 or max(width, height) > 1920 or min(width, height) > 1080:
        raise VideoError("최대 1920 × 1080 또는 세로형 1080 × 1920까지 지원합니다.")


def open_video(path):
    return av.open(
        str(path), format="mov", options={"enable_drefs": "0", "protocol_whitelist": "file"}
    )


def samples(path, duration_ms):
    """Pick the frame nearest each interval midpoint using presentation timestamps."""
    step = POLICY["sample_ms"]
    _, index = inspect_video(path)
    with open_video(path) as video:
        stream = video.streams[index]
        origin = stream.start_time * stream.time_base if stream.start_time is not None else None
        start, best, distance = 0, None, float("inf")
        for frame in video.decode(stream):
            check_dimensions(frame.width, frame.height)
            if frame.pts is None:
                continue
            timestamp = frame.pts * frame.time_base
            if origin is None:
                origin = timestamp
            pos = (timestamp - origin) * 1000
            if pos < start:
                continue
            while start < duration_ms and pos >= min(start + step, duration_ms):
                end = min(start + step, duration_ms)
                yield start, end, best
                start, best, distance = end, None, float("inf")
            if start >= duration_ms:
                break
            mid = (start + min(start + step, duration_ms)) / 2
            delta = abs(float(pos) - mid)
            if delta < distance:
                best, distance = frame, delta
        while start < duration_ms:
            end = min(start + step, duration_ms)
            yield start, end, best
            start, best = end, None


def assess_frame(frame):
    if frame is None:
        return None, "gap"
    image = frame.to_image().convert("RGB")
    rotation = int(frame.rotation) % 360
    if rotation:
        image = image.rotate(rotation, expand=True)
    gray = np.asarray(
        image.convert("L").resize((160, 120), Image.Resampling.BILINEAR), dtype=np.float32
    )
    lap = 4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    if (
        gray.mean() < POLICY["min_brightness"]
        or gray.std() < POLICY["min_contrast"]
        or lap.var() < POLICY["min_sharpness"]
    ):
        return None, "quality"
    return image, None


def analyze(path, duration_ms, predictor, progress):
    from .predictor import checked_predictions

    segments = []
    batch = []

    def flush():
        images = [image for _, _, image, _ in batch if image is not None]
        decisions = iter(checked_predictions(predictor, images)) if images else iter(())
        for start, end, image, reason in batch:
            label = None
            if image is not None:
                row = next(decisions)
                idx = int(row.argmax())
                if row[idx] >= POLICY["min_confidence"]:
                    label = f"c{idx}"
                else:
                    reason = "confidence"
            append_segment(segments, start, end, label, reason)
        progress(batch[-1][1])
        batch.clear()

    for start, end, frame in samples(path, duration_ms):
        image, reason = assess_frame(frame)
        batch.append((start, end, image, reason))
        if len(batch) == 8:
            flush()
    if batch:
        flush()
    return score(segments, duration_ms), segments

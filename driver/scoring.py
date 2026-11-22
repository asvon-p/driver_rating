"""Time-weighted scoring. Every millisecond is either accepted once or excluded."""

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

CLASSES = (
    "일반 운전",
    "문자 입력 · 오른손",
    "전화 통화 · 오른손",
    "문자 입력 · 왼손",
    "전화 통화 · 왼손",
    "라디오 조작",
    "음료 마시기",
    "뒤로 팔 뻗기",
    "머리 손질·화장",
    "동승자와 대화",
)
POLICY = {
    "risks": [0, 100, 70, 100, 70, 40, 50, 90, 80, 30],
    "sample_ms": 500,
    "min_confidence": 0.70,
    "min_valid_ms": 10000,
    "min_coverage_percent": 70,
    "min_brightness": 16,
    "min_contrast": 9,
    "min_sharpness": 8,
}
POLICY_ID = (
    "time-v1-" + hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()[:10]
)
REASONS = {
    "gap": "프레임 없음",
    "quality": "어둡거나 불분명한 화면",
    "confidence": "자세 구분 불확실",
}


def tenth(n, d):
    return float((Decimal(n) / Decimal(d)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def append_segment(segments, start, end, label=None, reason=None):
    if end <= start or (label is None) == (reason is None):
        raise ValueError("Invalid time interval or decision")
    if segments and segments[-1]["end_ms"] != start:
        raise ValueError("Non-contiguous analysis")
    if segments and segments[-1]["label"] == label and segments[-1]["reason"] == reason:
        segments[-1]["end_ms"] = end
    else:
        segments.append({"start_ms": start, "end_ms": end, "label": label, "reason": reason})


def score(segments, duration_ms):
    times = {f"c{i}": 0 for i in range(len(CLASSES))}
    excluded = {reason: 0 for reason in REASONS}
    cursor = 0
    for item in segments:
        length = item["end_ms"] - item["start_ms"]
        if item["start_ms"] != cursor or length <= 0:
            raise ValueError("Invalid timeline")
        cursor = item["end_ms"]
        if item["label"] is None:
            excluded[item["reason"]] += length
        else:
            times[item["label"]] += length
    if cursor != duration_ms or duration_ms <= 0:
        raise ValueError("Incomplete timeline")
    valid = sum(times.values())
    weighted = sum(times[f"c{i}"] * risk for i, risk in enumerate(POLICY["risks"]))
    holds = []
    if valid < POLICY["min_valid_ms"]:
        holds.append("판정한 시간이 10초 미만입니다.")
    if valid * 100 < duration_ms * POLICY["min_coverage_percent"]:
        holds.append("전체 영상 중 판정한 시간의 비율이 70% 미만입니다.")
    return {
        "score": None if holds else tenth(100 * valid - weighted, valid),
        "coverage": tenth(100 * valid, duration_ms),
        "duration_ms": duration_ms,
        "valid_ms": valid,
        "excluded_ms": duration_ms - valid,
        "hold_reasons": holds,
        "excluded_by_reason": excluded,
        "classes": [
            {
                "code": f"c{i}",
                "name": name,
                "seconds": times[f"c{i}"] / 1000,
                "risk": POLICY["risks"][i],
                "deduction": tenth(times[f"c{i}"] * POLICY["risks"][i], valid) if valid else 0,
            }
            for i, name in enumerate(CLASSES)
        ],
    }

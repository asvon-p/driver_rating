"""Photo-level decisions and ordered, bias-corrected EMA. Stored results are self-contained."""
from collections import defaultdict
from fractions import Fraction

from .scoring import CLASSES, POLICY

ROLES = ("A", "B")
RULES = {
    "version": "image-ema-v1", "alpha": 0.30, "safe": 85, "ordinary": 60,
    "classes": list(CLASSES), "risks": list(POLICY["risks"]), "confidence": POLICY["min_confidence"],
    "quality": {k: POLICY[k] for k in ("min_brightness", "min_contrast", "min_sharpness")},
}


def grade(value, rules):
    if value >= rules["safe"]:
        return "안전 운전"
    return "일반 운전" if value >= rules["ordinary"] else "위험 운전"


def decision(probabilities, rules):
    values = [float(x) for x in probabilities]
    label = max(range(len(values)), key=values.__getitem__)
    risk = rules["risks"][label]
    output = {"label": label, "confidence": values[label], "probabilities": values, "risk": risk, "score": None}
    if values[label] < rules["confidence"]:
        return dict(output, reason="신뢰도 기준 미달")
    return dict(output, score=100 - risk, grade=grade(100 - risk, rules))


def summarize(results, rules):
    alpha = Fraction(str(rules["alpha"]))
    decay = 1 - alpha
    numerator = denominator = Fraction(0)
    trace, valid, actions = [], [], defaultdict(list)
    for pos, item in enumerate(results):
        if item.get("score") is None:
            trace.append(None)
            continue
        valid.append(pos)
        actions[item["label"]].append(pos + 1)
        numerator = decay * numerator + alpha * item["score"]
        denominator = decay * denominator + alpha
        trace.append(float(numerator / denominator))
    weights = [None] * len(results)
    for elapsed, pos in enumerate(reversed(valid)):
        weights[pos] = float(alpha * decay ** elapsed / denominator)
    final = numerator / denominator if valid else None
    return {
        "valid": len(valid), "total": len(results), "trace": trace, "weights": weights,
        "last": valid[-1] + 1 if valid else None, "actions": dict(actions),
        "ema": float(final) if final is not None else None,
        "grade": grade(final, rules) if final is not None else None,
        "change": float(final) - trace[valid[0]] if len(valid) > 1 else None,
    }


def compare(photos, rules):
    summaries = {role: summarize([p["results"][role] for p in photos], rules) for role in ROLES}
    common = [p for p in photos if all(p["results"][r].get("score") is not None for r in ROLES)]
    scores = {role: summarize([p["results"][role] for p in common], rules)["ema"] for role in ROLES}
    return {"models": summaries, "common": {"count": len(common), "scores": scores,
            "delta": scores["B"] - scores["A"] if len(common) >= 2 else None}}

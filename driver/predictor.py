"""Trusted model manifest -> interchangeable RGB batch predictor."""

import hashlib
import importlib
import json
import os
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torchvision import transforms
from torchvision.models import resnet50

from local_runtime import ROOT


class ResNetEnsemble:
    def __init__(self, manifest, paths):
        setting = os.getenv("DRIVER_DEVICE", "auto")
        self.device = torch.device(
            ("cuda" if torch.cuda.is_available() else "cpu") if setting == "auto" else setting
        )
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        if self.device.type == "cpu":
            torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.transform = transforms.Compose(
            [
                transforms.Resize(tuple(manifest["image_size"])),
                transforms.ToTensor(),
                transforms.Normalize(manifest["mean"], manifest["std"]),
            ]
        )
        self.models = []
        self.temperature = float(manifest.get("temperature", 1))
        if not np.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("Invalid temperature")
        for path in paths:
            model = resnet50(weights=None)
            model.fc = nn.Linear(model.fc.in_features, 10)
            model.load_state_dict(
                torch.load(path, map_location="cpu", weights_only=True)["model_state"], strict=True
            )
            self.models.append(model.to(self.device).eval())

    @torch.inference_mode()
    def predict_batch(self, images):
        x = torch.stack([self.transform(im.convert("RGB")) for im in images]).to(self.device)
        probabilities = [
            torch.softmax(model(x).double() / self.temperature, dim=1) for model in self.models
        ]
        return torch.stack(probabilities).mean(dim=0).cpu().numpy()


def checked_predictions(predictor, images):
    out = np.asarray(predictor.predict_batch(images))
    if (
        out.shape != (len(images), 10)
        or not np.isfinite(out).all()
        or (out < 0).any()
        or (out > 1).any()
        or not np.allclose(out.sum(axis=1), 1, atol=1e-6, rtol=0)
    ):
        raise ValueError("Invalid model probabilities")
    return out


def load_model():
    from PIL import Image

    path = Path(os.getenv("DRIVER_MODEL_CONFIG", ROOT / "models" / "team-resnet50.json")).resolve()
    manifest = json.loads(path.read_text("utf-8"))
    if manifest["classes"] != [f"c{i}" for i in range(10)]:
        raise ValueError("The model manifest must map columns to c0 through c9 in order")
    root = path.parent / manifest["root"]
    paths = [(root / name).resolve() for name in manifest["checkpoints"]]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("Missing or duplicate model paths")
    hasher = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode())
    for entry in paths:
        with entry.open("rb") as stream:
            hasher.update(hashlib.file_digest(stream, "sha256").digest())
    module, name = manifest["adapter"].split(":")
    predictor = getattr(importlib.import_module(module), name)(manifest, paths)
    checked_predictions(predictor, [Image.new("RGB", (224, 224))])
    info = {
        "name": manifest["name"],
        "version": hasher.hexdigest()[:16],
        "folds": len(paths),
        "device": str(getattr(predictor, "device", "custom")),
    }
    return predictor, info

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


class SpatialAttention(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv = nn.Conv2d(2, 1, 7, padding=3, bias=False)

    def forward(self, x):
        pooled = torch.cat((x.mean(1, keepdim=True), x.amax(1, keepdim=True)), dim=1)
        return x * self.conv(pooled).sigmoid()


class ResNetEnsemble:
    def __init__(self, manifest, paths):
        setting = os.getenv("DRIVER_DEVICE", "auto")
        self.device = torch.device(("cuda" if torch.cuda.is_available() else "cpu") if setting == "auto" else setting)
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        if self.device.type == "cpu":
            torch.set_num_threads(min(4, os.cpu_count() or 1))
        self.size, width = manifest["image_size"]
        if width != self.size:
            raise ValueError("Expected square model input")
        self.zoom = manifest.get("zoom_size", self.size)
        if self.zoom < self.size:
            raise ValueError("Zoom crop is smaller than model input")
        self.r10 = manifest.get("architecture", "baseline") == "r10"
        self.temperature = float(manifest.get("temperature", 1))
        if not np.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("Invalid temperature")
        norm = [transforms.ToTensor(), transforms.Normalize(manifest["mean"], manifest["std"])]
        self.views = [transforms.Compose([transforms.Resize((self.size, self.size)), *norm])]
        if self.zoom != self.size:
            self.views.append(transforms.Compose([transforms.Resize((self.zoom, self.zoom)),
                                                  transforms.CenterCrop(self.size), *norm]))
        self.models = []
        for path in paths:
            model = resnet50(weights=None)
            weights = torch.load(path, map_location="cpu", weights_only=True)
            if self.r10:
                model.avgpool = nn.Sequential(SpatialAttention(), nn.AdaptiveAvgPool2d(1))
                model.fc = nn.Sequential(nn.Linear(2048, 512), nn.SiLU(), nn.Dropout(0.4), nn.Linear(512, 10))
            else:
                model.fc = nn.Linear(model.fc.in_features, 10)
                weights = weights["model_state"]
            model.load_state_dict(weights, strict=True)
            self.models.append(model.eval().requires_grad_(False).to(self.device))

    def inputs(self, images):
        return torch.stack([view(image) for image in images for view in self.views]).to(self.device)

    def fold_probabilities(self, model, inputs):
        with torch.autocast(self.device.type, enabled=self.r10 and self.device.type == "cuda"):
            logits = model(inputs)
        logits = logits.float() if self.r10 else logits.double()
        return (logits.reshape(-1, len(self.views), 10).mean(1) / self.temperature).softmax(dim=1)

    @torch.inference_mode()
    def predict_batch(self, images):
        x = self.inputs(images)
        return torch.stack([self.fold_probabilities(m, x).double() for m in self.models]).mean(0).cpu().numpy()

    @torch.enable_grad()
    def heatmap(self, image, target):
        """Grad-CAM of the ensemble class probability at the final spatial block."""
        from PIL import Image
        from torch.nn.functional import interpolate

        inputs = self.inputs([image])
        evidence = torch.zeros((self.zoom, self.zoom), device=self.device)
        coverage = torch.zeros_like(evidence)
        offset = round((self.zoom - self.size) / 2)  # torchvision CenterCrop
        for model in self.models:
            captured = []

            def capture(module, args, output):
                captured.append(output.detach())

            handle = model.layer4.register_forward_hook(capture)
            try:
                # Preserve the feature maps and logits of the actual TTA forward.
                with torch.no_grad(), torch.autocast(self.device.type, enabled=self.r10 and self.device.type == "cuda"):
                    logits = model(inputs)
                probs = (logits.double().mean(0) / self.temperature).softmax(0)
                # d softmax(z)[target] / dz, avoiding 1 - p cancellation at high confidence.
                other = probs.clone()
                other[target] = 0
                derivative = -probs[target] * other
                derivative[target] = probs[target] * other.sum()
                derivative /= self.temperature * len(self.models) * len(self.views)
                # A half-precision backward pass rounds small but meaningful gradients to zero.
                feature = captured[0].float().requires_grad_(True)
                head = model.fc(model.avgpool(feature).flatten(1))
                gradient, = torch.autograd.grad(head, feature, derivative.float().expand_as(head))
                cams = (gradient.float().mean((2, 3), keepdim=True) * feature.detach().float()).sum(1).relu()
                for i, cam in enumerate(cams):
                    side = self.zoom if i == 0 else self.size
                    origin = 0 if i == 0 else offset
                    region = (slice(origin, origin + side),) * 2
                    evidence[region] += interpolate(cam[None, None], size=(side, side),
                                                   mode="bilinear", align_corners=False)[0, 0]
                    coverage[region] += 1
            finally:
                handle.remove()
        evidence /= coverage.clamp_min(1)
        if not torch.isfinite(evidence).all() or evidence.max() <= 0:
            raise ValueError("이 자세의 양의 기여 영역을 계산할 수 없습니다.")
        evidence /= evidence.max()
        preview = image.copy()
        preview.thumbnail((960, 960))
        mask = np.asarray(Image.fromarray(evidence.cpu().numpy()).resize(preview.size, Image.Resampling.BILINEAR)).clip(0, 1)
        heat = np.stack([np.ones_like(mask), 0.85 * (1 - mask), 0.12 * (1 - mask)], axis=-1) * 255
        opacity = 0.72 * mask[..., None]
        return Image.fromarray((np.asarray(preview) * (1 - opacity) + heat * opacity).astype("uint8"))


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


def load_model(config=None):
    from PIL import Image

    path = Path(config or os.getenv("DRIVER_MODEL_CONFIG", ROOT / "models" / "team-resnet50.json")).resolve()
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
        "description": manifest.get("description", ""),
        "provisional": bool(manifest.get("provisional", False)),
        "device": str(getattr(predictor, "device", "custom")),
    }
    return predictor, info

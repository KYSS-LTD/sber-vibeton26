"""Verified model download and one-time CPU ONNX conversion."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
from .config import MODEL_DIR


def download(url, path, sha=None):
    if path.exists() and (
        sha is None or hashlib.sha256(path.read_bytes()).hexdigest() == sha
    ):
        return
    request = urllib.request.Request(
        url, headers={"User-Agent": "Prohod-model-init/1.0"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if sha and hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError("Model checksum mismatch: " + path.name)
    path.write_bytes(data)


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(
        (Path(__file__).parent.parent / "models.json").read_text(encoding="utf-8")
    )
    for item in manifest.values():
        download(item["url"], MODEL_DIR / item["file"], item["sha256"])
    if (
        not (MODEL_DIR / "yolov8n-face.onnx").exists()
        or not (MODEL_DIR / "minifas.onnx").exists()
    ):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(Path(__file__).parent.parent / "requirements-models.lock"),
            ],
            check=True,
        )
        import torch

        os.environ["YOLO_AUTOINSTALL"] = "false"
        os.environ["YOLO_CONFIG_DIR"] = str(MODEL_DIR / ".export-config")
        from ultralytics import YOLO

        if not (MODEL_DIR / "yolov8n-face.onnx").exists():
            YOLO(str(MODEL_DIR / "yolov8n-face.pt")).export(
                format="onnx", imgsz=640, simplify=False, opset=17, device="cpu"
            )
        if not (MODEL_DIR / "minifas.onnx").exists():
            source = MODEL_DIR / "MiniFASNet.py"
            spec = importlib.util.spec_from_file_location(
                "minifas_architecture", source
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            model = module.MiniFASNetV2(conv6_kernel=(5, 5))
            weights = torch.load(
                str(MODEL_DIR / "minifas.pth"), map_location="cpu", weights_only=True
            )
            model.load_state_dict(
                {k.removeprefix("module."): v for k, v in weights.items()}
            )
            model.eval()
            torch.onnx.export(
                model,
                torch.zeros(1, 3, 80, 80),
                str(MODEL_DIR / "minifas.onnx"),
                input_names=["image"],
                output_names=["logits"],
                opset_version=17,
                dynamo=False,
            )
    (MODEL_DIR / "installed.json").write_text(
        json.dumps(
            {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in MODEL_DIR.glob("*.onnx")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

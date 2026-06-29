"""ml-worker 진입점 — 모델 1회 적재 후 ocr_jobs 폴링."""

import os
import time
from pathlib import Path

from worker.db import WorkerQueue, build_engine
from worker.poll import process_one_job

POLL_INTERVAL_SEC = 2.0


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} 미설정")
    return val


def load_models():
    """품목 인코더(CPU torch) + 금액 인식기(MLX Metal) 1회 적재. device 분리 보존."""
    import numpy as np

    from handwriting import infer_photo as ip

    models_dir = Path(_require("SJMJ_ML_MODELS_DIR"))
    device = "cpu"  # PyTorch-MPS와 MLX Metal 동시 사용 시 degenerate — CPU 고정
    item_model = ip.load_model_from(models_dir / "ft_prod.pt", device)
    z = np.load(models_dir / "bank.npz", allow_pickle=True)
    qwen = ip.load_ocr()
    return item_model, z["emb"], list(z["lab"]), qwen, device


def main():
    from handwriting.infer_job import infer_job

    crops_root = Path(_require("SJMJ_DATA_DIR")) / "ocr_crops"
    queue = WorkerQueue(build_engine())
    models = load_models()

    def infer_fn(image_path, crop_dir, job_id):
        return infer_job(image_path, models, crop_dir, job_id)

    while True:
        worked = process_one_job(queue, infer_fn, crops_root)
        if not worked:
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()

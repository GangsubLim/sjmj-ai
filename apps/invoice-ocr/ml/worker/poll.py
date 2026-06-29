"""한 잡 처리 단위. 잡 단위 격리 — 한 잡 실패가 워커를 죽이지 않는다."""

from pathlib import Path


def process_one_job(queue, infer_fn, crops_root) -> bool:
    job = queue.claim_next_pending()
    if job is None:
        return False
    crop_dir = Path(crops_root) / f"job-{job['id']}"
    try:
        result = infer_fn(job["image_path"], crop_dir, job["id"])
        queue.mark_done(job["id"], result)
    except Exception as exc:  # noqa: BLE001 — 잡 단위 격리(워커 생존)
        queue.mark_failed(job["id"], {"error": str(exc)})
    return True

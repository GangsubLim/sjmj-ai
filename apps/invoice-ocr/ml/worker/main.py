"""ml-worker 진입점 — 모델 1회 적재 후 ocr_jobs 폴링."""

import os
import sys
import time
from pathlib import Path
from typing import NamedTuple

from worker.db import WorkerQueue, build_engine
from worker.poll import process_one_job

POLL_INTERVAL_SEC = 2.0
# 지문 입력 파일명. handwriting.bank_id의 동명 상수와 같은 값이어야 한다 — 워커가 추론에 쓴
# 파일과 지문이 해시·집계하는 파일이 갈라지면 두 소비자(워커·원격 분석 스크립트)의 지문이
# 전량 어긋나 모든 잡이 조용히 stale로 오분류된다. 상수를 import해 공유하지 않는 이유는
# bank_id import 자체가 retrieval_version_or_none의 fail-safe 안에 있어야 하기 때문이다
# (그 import 실패가 기동을 깨면 안 된다). 리터럴 일치는 tests/test_worker_models.py가 고정한다.
MODEL_FILENAME = "ft_prod.pt"
BANK_FILENAME = "bank.npz"


class ModelBundle(NamedTuple):
    """load_models 산출 — infer_job이 **속성으로** 읽는다(위치 언패킹 금지).

    5-튜플에 6번째 원소를 위치로 더하면 순서 실수가 조용히 통과한다. 소비자는
    worker.main.main과 handwriting.infer_job.infer_job 둘뿐이라 승격 비용이 낮다.
    """

    item_model: object
    emb: object
    labs: list[str]
    qwen: object
    device: str
    retrieval_version: str | None = None


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} 미설정")
    return val


def retrieval_version_or_none(models_dir, npz, labs) -> str | None:
    """이 워커 세션이 쓴 retrieval 지문을 계산한다. 어떤 실패도 밖으로 내지 않는다.

    지문 입력(모델 파일명·뱅크 key 추출)은 `bank_id.bank_retrieval_version` 하나가 고른다 —
    분석 도구의 원격 스크립트도 같은 함수를 부른다(M4). 그 입력을 양쪽이 각자 복붙하면 한쪽만
    바뀔 때 두 지문이 어긋나 모든 잡이 조용히 stale로 오분류된다.

    z["keys"]는 지문 전용 입력이고 현행 워커는 emb/lab만 있으면 기동한다(load_models 참조).
    keys 부재·손상은 진단 필드 하나의 실패여야 하고 운영 중단이어서는 안 된다(spec §3-A,
    Global Constraint). validate_bank_arrays의 docstring이 "워커는 시작 시 emb/lab만
    적재한다"고 명시하므로 keys 없는 뱅크는 실재 가능한 상태다.

    Args:
        models_dir: 품목 인코더(ft_prod.pt)와 bank.npz가 사는 디렉터리.
        npz: 적재된 bank.npz(여기서 `keys`·`emb`를 읽는다).
        labs: 뱅크 label 열.

    Returns:
        12자 지문. 계산에 실패하면 None(자리표시자를 만들지 않는다).
    """
    try:
        from handwriting import bank_id

        return bank_id.bank_retrieval_version(models_dir, npz, labs)
    except Exception as e:
        # 광범위 포착이 의도다 — KeyError(keys 부재)·ValueError(뱅크 계약 위반)·OSError(파일
        # 부재)·bank_id 자체의 import 실패도 상시 워커를 죽이지 못한다. stderr로 남긴다 —
        # bank_id.code_version도 같은 창구(ml-worker.err.log)를 쓰므로 원인 추적이 한 파일로
        # 모인다. models_dir을 넣어 어떤 뱅크가 문제인지 알 수 있게 한다.
        print(
            f"[retrieval-version] 지문 계산 실패(models_dir={models_dir}, "
            f"{type(e).__name__}: {e}) — 스탬프 생략",
            file=sys.stderr,
            flush=True,
        )
        return None


def load_models() -> ModelBundle:
    """품목 인코더(CPU torch) + 금액 인식기(MLX Metal) 1회 적재. device 분리 보존.

    지문 입력(`keys`)은 `retrieval_version_or_none`이 fail-safe 안에서 읽는다 — 추론 필수
    입력(`emb`·`lab`)만 여기서 직접 읽는다. 뱅크는 여기서 1회만 적재되므로(재시작 전까지 파일
    변경이 추론에 반영되지 않는다) 이 지문이 곧 '이 워커 세션이 쓴 retrieval 상태'다.
    """
    import numpy as np

    from handwriting import infer_photo as ip

    models_dir = Path(_require("SJMJ_ML_MODELS_DIR"))
    device = "cpu"  # PyTorch-MPS와 MLX Metal 동시 사용 시 degenerate — CPU 고정
    model_path = models_dir / MODEL_FILENAME
    item_model = ip.load_model_from(model_path, device)
    z = np.load(models_dir / BANK_FILENAME, allow_pickle=True)
    emb = z["emb"]
    labs = [str(x) for x in z["lab"]]
    qwen = ip.load_ocr()
    retrieval_version = retrieval_version_or_none(models_dir, z, labs)
    # 로그↔DB 대조로 스탬프 소실을 즉시 알 수 있도록 부팅 시 지문을 한 줄 남긴다.
    print(
        f"[retrieval-version] 부팅 지문={retrieval_version or '없음'}", file=sys.stderr, flush=True
    )
    return ModelBundle(
        item_model=item_model,
        emb=emb,
        labs=labs,
        qwen=qwen,
        device=device,
        retrieval_version=retrieval_version,
    )


def main():
    """모델을 1회 적재한 뒤 ocr_jobs를 무한 폴링하며 처리한다."""
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

"""품목 인코더 재학습의 학습 입력 구성·실험계획·채점 계층(#133 AC1·AC2).

부트스트랩 크롭 npz(SP2 잔재)와 큐레이션 학습쌍 크롭(ocr_crops/job-N/row-K.png)을 한 벌의
TrainSet으로 모으고, 잡 단위 hold-out 격자를 torch 없이 계획·채점한다. 모집단 규칙(ADR 0004
검수 게이트)과 제외 축·top-k 규칙은 tools.bank_update의 순수함수를 그대로 호출한다 —
재구현하면 뱅크 갱신과 학습 입력이 서로 다른 모집단을 보게 된다.

코어 규약 준수: 모듈 레벨은 stdlib + bank_update(stdlib 전용)까지. numpy/cv2/fewshot(torch)은
함수 본문 지연 import라 paddle-free venv에서도 import가 성공하고 CI가 이 모듈을 테스트한다.
"""

from dataclasses import dataclass
from pathlib import Path

from tools.bank_update import inv_of, partition_crop_ref, partition_valid, select_desired

CROP_SIZE = 224
# 실험계획 재현 seed — 학습 seed(train_contrastive.SEED)와 별개 축이다.
SEED = 20260828
# 부트스트랩 npz의 필수 배열. 실파일에는 prepare()가 남긴 캐시 `key`가 하나 더 있으므로
# 상등이 아니라 부분집합으로 검증한다(2026-08-28 실측: files=['key','sq','lab','inv','keys']).
BOOTSTRAP_ARRAYS = ("sq", "lab", "inv", "keys")
ORIGINS = ("bootstrap", "curated")


@dataclass(frozen=True)
class TrainSet:
    """학습 입력 한 벌 — 다섯 열의 길이가 항상 같다.

    sq는 224² RGB uint8 배열의 튜플이고, origin은 항목별 출처(bootstrap|curated)다.
    출처를 집합 단위가 아니라 항목 단위로 두는 이유는 merge 이후에도 어느 항목이
    부트스트랩인지 남아야 실험계획이 '부트스트랩은 항상 train' 규칙을 세울 수 있기 때문이다.
    """

    sq: tuple
    lab: tuple[str, ...]
    inv: tuple[str, ...]
    keys: tuple[str, ...]
    origin: tuple[str, ...]


def load_bootstrap(npz_path) -> TrainSet:
    """부트스트랩 크롭 npz를 적재한다 — 배열 누락·크롭 규격 불일치는 즉시 실패.

    `allow_pickle=True`가 필요한 이유는 `lab`/`inv`/`keys`가 object dtype(가변 길이 문자열)
    이기 때문이다(`bank_update.load_bank`와 동일 패턴). 입력은 1st-party 산출물(SP2 스파이크가
    만든 npz) 전제이며 임의 외부 파일을 신뢰 경계 없이 적재한다.
    """
    import numpy as np

    z = np.load(Path(npz_path), allow_pickle=True)
    missing = [k for k in BOOTSTRAP_ARRAYS if k not in z.files]
    if missing:
        raise ValueError(f"부트스트랩 npz 배열 누락 {missing} — 있는 배열: {sorted(z.files)}")
    sq = z["sq"]
    if sq.ndim != 4 or tuple(sq.shape[1:]) != (CROP_SIZE, CROP_SIZE, 3):
        raise ValueError(f"크롭 규격 불일치 {sq.shape} — (N,{CROP_SIZE},{CROP_SIZE},3)이어야 한다")
    lab, inv, keys = z["lab"].tolist(), z["inv"].tolist(), z["keys"].tolist()
    if not len(sq) == len(lab) == len(inv) == len(keys):
        raise ValueError(f"열 길이 불일치: sq{len(sq)}/lab{len(lab)}/inv{len(inv)}/keys{len(keys)}")
    return TrainSet(
        sq=tuple(sq),
        lab=tuple(lab),
        inv=tuple(inv),
        keys=tuple(keys),
        origin=("bootstrap",) * len(keys),
    )


def select_curated(pairs: list[dict], reviewed_job_ids: set[int]) -> list[dict]:
    """학습 대상 큐레이션 쌍을 고른다 — 게이트 순서는 bank_update._desired_pairs와 동일."""
    crop_ref_ok, _ = partition_crop_ref(select_desired(pairs, reviewed_job_ids))
    valid, _ = partition_valid(crop_ref_ok)
    return valid


def load_curated(pairs: list[dict], crops_root, *, square_fn=None) -> TrainSet:
    """큐레이션 크롭 PNG를 적재한다 — 누락은 전량 목록과 함께 즉시 실패.

    square_fn 기본값은 운영 임베딩과 같은 handwriting.fewshot.square다. 그 모듈은 모듈 레벨
    torch 의존이라 주입 슬롯을 둬야 CI(torch 없음)가 이 함수를 테스트할 수 있다
    (bank_update의 embed_fn 주입과 같은 관용구).
    """
    import cv2
    import numpy as np

    if square_fn is None:
        from handwriting.fewshot import square as square_fn

    root = Path(crops_root)
    missing = sorted({p["crop_ref"] for p in pairs if not (root / f"{p['crop_ref']}.png").exists()})
    if missing:
        raise FileNotFoundError(f"크롭 PNG 없음 {len(missing)}건: {missing}")

    sq, lab, inv, keys = [], [], [], []
    for p in pairs:
        ref = p["crop_ref"]
        img = cv2.imread(str(root / f"{ref}.png"))
        if img is None:
            raise RuntimeError(f"크롭 이미지를 읽을 수 없습니다: {ref}")
        sq_i = square_fn(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        if getattr(sq_i, "shape", None) != (CROP_SIZE, CROP_SIZE, 3) or sq_i.dtype != np.uint8:
            raise ValueError(f"square_fn 산출 규격 불일치 {ref}: {getattr(sq_i, 'shape', None)}")
        sq.append(sq_i)
        lab.append(p["canonical_label"])
        inv.append(inv_of(ref))
        keys.append(ref)
    return TrainSet(
        sq=tuple(sq),
        lab=tuple(lab),
        inv=tuple(inv),
        keys=tuple(keys),
        origin=("curated",) * len(keys),
    )


def merge(*sets: TrainSet) -> TrainSet:
    """TrainSet들을 이어 붙인다 — key 중복은 거부(같은 크롭이 두 번 학습되는 사고 차단)."""
    seen: set[str] = set()
    for s in sets:
        dup_within = {k for k in s.keys if s.keys.count(k) > 1}
        if dup_within:
            raise ValueError(f"TrainSet 내부 key 중복 {sorted(dup_within)}")
        dup = seen & set(s.keys)
        if dup:
            raise ValueError(f"key 중복 {sorted(dup)} — 같은 크롭이 두 벌 들어왔다")
        seen |= set(s.keys)
    return TrainSet(
        sq=tuple(x for s in sets for x in s.sq),
        lab=tuple(x for s in sets for x in s.lab),
        inv=tuple(x for s in sets for x in s.inv),
        keys=tuple(x for s in sets for x in s.keys),
        origin=tuple(x for s in sets for x in s.origin),
    )

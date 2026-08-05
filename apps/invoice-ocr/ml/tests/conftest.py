"""공용 픽스처. 실데이터에 의존하지 않는 합성 데이터만 둔다."""

import math

import pytest

from handwriting.warp_gate import MIN_BLUE_RATIO  # stdlib만 쓰는 모듈이라 코어 venv에서도 안전
from tools.curation_enrich import enrich_pairs  # 동일 — stdlib 전용 순수 계층

# 정상 합성의 파랑 비율을 임계의 몇 배로 둘지. 3배면 임계가 흔들려도 정상 케이스가 여유를 갖는다.
HEALTHY_RATIO_FACTOR = 3


@pytest.fixture
def make_warped():
    """합성 워프 BGR(900×2100) 생성기 — cv2/numpy가 있는 테스트에서만 요청한다.

    흰 바탕에 파랑(BGR 255,120,40) 수평 격자선을 그린다. grid_v4.hline_ys는 폭
    WARP_W//3(=300) 이상의 수평 런만 선으로 인정하므로 x_end로 '반쪽만 격자'(잡 39 유형)를,
    n_lines/pitch로 '격자 희박·피치 발산'(잡 34 유형)을 만든다.

    ⚠️ 픽스처 본문은 importorskip으로 시작한다. numpy/grid_v4 import는 테스트 본문의
       importorskip보다 **먼저** 실행되므로, 가드가 없으면 cv2 부재 환경에서 skip이 아니라
       fixture ERROR가 나고 '코어 paddle-free 회귀' 게이트가 빨갛게 죽는다.

    thickness=None(기본)이면 MIN_BLUE_RATIO에서 유도해 정상 합성의 파랑 비율이 임계의
    HEALTHY_RATIO_FACTOR배 근처가 되게 한다 — 고정 두께였다면 Task 7에서 MIN_BLUE_RATIO가
    캘리브될 때 테스트가 무조건 깨진다. 두께 하한은 3(ceil 바닥값)이지만 현재 상수
    (MIN_BLUE_RATIO=0.11)에서는 유도식이 지배해 thickness=28, 실효 비율 ~0.335가 나온다
    (바닥값 3은 MIN_BLUE_RATIO가 훨씬 작을 때만 적용됨).
    n_lines는 유도하지 않는다: y_start=620·pitch=83에서 DATA_Y 창에 들어가는 선은 최대 17개라
    MIN_HLINES에서 유도하면 임계가 14 이상일 때 그린 선과 검출 선 개수가 어긋난다(실측 확인).

    color(기본 BGR 255,120,40=강한 파랑)로 선 색을 바꿀 수 있다 — 옅은 파랑(b−r=10 등
    표준 마스크 임계 경계값)으로 enh 축 전용 입력을 만들 때 격자 좌표(y_start/pitch/
    thickness)는 그대로 재사용하려는 호출자를 위한 것.
    """
    pytest.importorskip("cv2", exc_type=ImportError)
    np = pytest.importorskip("numpy")

    from handwriting.grid_v4 import DATA_Y, WARP_H, WARP_W

    def _make(
        *, n_lines=16, pitch=83, y_start=620, x_end=WARP_W, thickness=None, color=(255, 120, 40)
    ):
        if thickness is None:
            span = DATA_Y[1] - DATA_Y[0]
            thickness = max(
                3, math.ceil(HEALTHY_RATIO_FACTOR * MIN_BLUE_RATIO * span / max(n_lines, 1))
            )
        img = np.full((WARP_H, WARP_W, 3), 255, np.uint8)
        for k in range(n_lines):
            y = y_start + k * pitch
            img[y : y + thickness, 0:x_end] = color
        return img

    return _make


def _reeval_record(crop_ref="job-1/row-0", side="after", axis="invoice", **over):
    """score.jsonl 레코드 shape의 합성 헬퍼 — reeval_gate/curation_report 테스트가 공유한다.

    private 헬퍼를 다른 테스트 모듈에서 `from tests.test_curation_cohort import _reeval_record`로
    끌어오면 수집 순서·리팩터 내성이 약하다(L3). conftest.py가 공유 픽스처의 관용적 자리다.
    """
    base = {
        "side": side,
        "axis": axis,
        "crop_ref": crop_ref,
        "label": "안가방",
        "in_bank": True,
        "top1": True,
        "top5": True,
        "has_peer": True,
        "preds": ["안가방", "공임"],
        "top1_sim": 0.91,
    }
    return {**base, **over}


# --- 큐레이션 리포트 계열 합성 헬퍼 (test_curation_enrich·test_curation_report·
# test_curation_render 공유) ---
# 분석 계층(tools/curation_enrich.py)과 렌더 계층(tools/curation_render.py)이 별도 모듈로
# 갈리며 같은 합성 입력을 양쪽이 쓴다 — _reeval_record와 같은 이유로 conftest에 둔다(L3).

# frozenset — 모듈 전역 공유 픽스처라 한 테스트가 add/discard하면 수집 순서에 따라 다른
# 테스트의 버킷 판정이 바뀐다(프로젝트 불변성 규약).
BANK = frozenset({"엔진오일", "드라이", "타이어", "공임"})
_CUR_VERSION = "cur-fingerprint"


def _four_vintages(crop_ref="job-1/row-0"):
    """score.jsonl은 같은 crop_ref에 (side, axis) 4벌을 담는다(bank_update.py:_write_score_artifacts)."""
    return [
        _reeval_record(crop_ref, side=side, axis=axis)
        for side in ("before", "after")
        for axis in ("crop_ref", "invoice")
    ]


def _reeval_meta(**over):
    """bank_update.score_meta가 내는 score_meta.json shape — 지문은 중첩 구조다(before/after)."""
    base = {
        "generated_at": "2026-07-30T05:12:00+09:00",
        "scope": "all",
        "axes": ["crop_ref", "invoice"],
        "n_pairs": 1,
        "retrieval_version": {"before": "old", "after": _CUR_VERSION},
        "score_jsonl_sha256": "digest-1",
    }
    return {**base, **over}


def _pair(**over):
    base = {
        "id": 1,
        "crop_ref": "job-1/row-0",
        "job_id": 1,
        "row_index": 0,
        "draft_label": "엔진오일",
        "final_label": "엔진오일",
        "canonical_label": "엔진오일",
        "supply": 100000,
        "status": "included",
        "exclusion_reason": None,
        "reviewed_at": None,
    }
    return {**base, **over}


def _job(job_id=1, rows=None, retrieval_version=_CUR_VERSION):
    """result_json 1건 — rows의 crop_ref가 이 잡에 속하는지 조립 시점에 검증한다.

    `_job(job_id=...)`와 `_row(job=...)`는 기본값이 독립이라 한쪽만 바꾸면 조용히 어긋난다.
    그러면 pair의 crop_ref가 rows_by_ref에서 안 잡혀 검증하려던 버킷이 아니라 row_missing이
    나오고, 테스트는 그 사실을 모른 채 통과한다 — 그래서 즉시 실패시킨다(덮어쓰기가 아니라
    실패로 막는 이유: crop_ref 불일치를 의도적으로 쓰는 테스트의 의도를 지워버리지 않는다).
    """
    rows = rows or []
    strays = [r["crop_ref"] for r in rows if not r["crop_ref"].startswith(f"job-{job_id}/")]
    if strays:
        raise AssertionError(f"_job(job_id={job_id})에 다른 잡의 행이 섞였다: {strays}")
    result = {"rows": rows, "warp_ok": True}
    if retrieval_version is not None:
        result["retrieval_version"] = retrieval_version
    return {"job_id": job_id, "image_path": f"/data/up/{job_id}.jpeg", "result": result}


def _row(idx=0, top5=None, supply=100000, raw="100", job=1):
    return {
        "row_index": idx,
        "crop_ref": f"job-{job}/row-{idx}",
        "item_top5": [{"label": lb, "sim": s} for lb, s in (top5 or [])],
        "supply": supply,
        "amount_raw": raw,
    }


def _enrich(pairs, jobs, bank=BANK, **kw):
    """기본 현재 지문을 물려주는 래퍼 — 스탬프를 명시하지 않은 테스트는 current_bank가 된다."""
    kw.setdefault("current_retrieval_version", _CUR_VERSION)
    return enrich_pairs(pairs, jobs, bank, **kw)


def _enriched_row(**over):
    """소비자 술어 테스트용 최소 enriched 행(전 잡 폭주 회귀를 합성으로 재현한다)."""
    base = {
        "job_id": 1,
        "crop_ref": "job-1/row-0",
        "status": "included",
        "exclusion_reason": None,
        "answer": "안가방",
        "final_label": "안가방",
        "draft_label": "안가방",
        "supply": 100000,
        "draft_supply": 100000,
        "amount_raw": "100",
        "top5_labels": [],
        "top1_sim": None,
        "in_bank": True,
        "label_bucket": "unevaluable",
        "amount_bucket": "ok",
        "cohort": "current_bank",
        "reeval_has_peer": None,
    }
    return {**base, **over}


def _correction(
    job_id=1, *, n_lines=3, rows_added=0, rows_dropped=0, has_correction=True, n_corrections=1
):
    """corrections.json 1행 합성 — parse_corrections_tsv 출력 shape과 한 벌이다.

    n_lines=None이면 행 수지 미상(다섯 값 모두 None)이다. 미상 두 종은 has_correction으로
    가른다(교정 이력 없음 vs 교정 JSON 결손).

    두 값을 독립 인자로 받되 **어긋나면 즉시 실패시킨다**(_job의 crop_ref 검증과 같은 이유):
    파서는 `has_correction = n_corrections > 0`으로 파생하므로 둘이 어긋난 행은 production이
    낼 수 없다. 그 상태로 합성하면 두 필드를 서로 다르게 읽는 소비자
    (`summarize_row_balance`의 n_no_correction_jobs vs n_multi_correction_jobs)를 두고
    의미가 같은 리팩터가 RED로 뜬다. 교정 이력 없음은 `n_corrections=0`으로 표현한다.
    """
    if has_correction != (n_corrections > 0):
        raise AssertionError(
            f"has_correction={has_correction}는 n_corrections={n_corrections}와 어긋난다"
            " — 파서는 has_correction = n_corrections > 0으로 파생한다"
        )
    if n_lines is None:
        balance = dict.fromkeys(
            ("rows_added", "rows_dropped", "n_lines", "draft_rows", "confirmed_rows")
        )
    else:
        balance = {
            "rows_added": rows_added,
            "rows_dropped": rows_dropped,
            "n_lines": n_lines,
            "draft_rows": n_lines + rows_dropped,
            "confirmed_rows": n_lines + rows_added,
        }
    return {
        "job_id": job_id,
        "n_corrections": n_corrections,
        "has_correction": has_correction,
        **balance,
        "image_path": f"/data/up/{job_id}.jpeg",
    }


@pytest.fixture
def tiny_invoices_sql() -> str:
    """invoices/invoice_items 최소 INSERT 샘플 (백업 형식 모사)."""
    return (
        "INSERT INTO `invoices` (`id`, `document_title`, `issue_date`, `recipient`, "
        "`recipient2`, `vehicle_no`, `memo`, `show_stamp`, `issuer_id`, `total_supply`, "
        "`total_vat`, `grand_total`, `created_at`, `updated_at`) VALUES\n"
        "(11, '거래명세서', '2026-05-12', '옥천운수', '이희원', '5608', '', 1, NULL, "
        "300000, 30000, 330000, '2026-05-12 05:57:39', '2026-05-12 05:57:39'),\n"
        "(12, '거래명세서', '2026-05-13', '성우항공', NULL, '3102', 'O''Brien 메모', 1, NULL, "
        "120000, 12000, 132000, '2026-05-13 08:48:53', '2026-05-13 08:48:53');\n"
        "INSERT INTO `invoice_items` (`id`, `invoice_id`, `item_order`, `name`, `quantity`, "
        "`unit`, `unit_price`, `supply`, `vat`, `total`) VALUES\n"
        "(42, 11, 1, '단지', 1, 'EA', 300000, 300000, 30000, 330000),\n"
        "(43, 12, 1, '세차', 1, 'EA', 30000, 30000, 3000, 33000),\n"
        "(44, 12, 2, '중고타이어', 1, NULL, 90000, 90000, 9000, 99000);\n"
    )

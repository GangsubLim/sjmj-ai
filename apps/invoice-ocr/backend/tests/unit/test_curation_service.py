"""CurationService 단위 테스트 — repository는 mock, DB 비의존."""

from app.services.curation_service import CurationService


def _pair(pair_id: int, row_index: int) -> dict:
    return {
        "id": pair_id,
        "crop_ref": f"job-1/row-{row_index}",
        "row_index": row_index,
        "draft_label": "무우",
        "final_label": "무",
        "canonical_label": "무",
        "supply": 8000,
        "status": "included",
        "exclusion_reason": None,
        "reviewed_at": None,
    }


class _Repo:
    def __init__(self, result_json, pairs: list[dict] | None = None):
        self._result_json = result_json
        self._pairs = [_pair(1, 0)] if pairs is None else pairs

    def find_job_detail(self, job_id: int) -> dict:
        return {
            "job": {
                "id": job_id,
                "invoice_id": 10,
                "curation_reviewed": 0,
                "created_at": "2026-07-28T09:00:00",
                "result_json": self._result_json,
            },
            "pairs": self._pairs,
        }


def test_detail_pair_exposes_exclusion_reason():
    repo = _Repo({"rows": []}, pairs=[{**_pair(1, 0), "exclusion_reason": "blank_crop"}])
    detail = CurationService(repo).get_detail(1)
    assert detail["pairs"][0]["exclusion_reason"] == "blank_crop"


def test_pair_carries_uncertain_flag_from_result_json():
    result = {
        "rows": [
            {"row_index": 0, "item_top5": [{"label": "무", "sim": 0.4}], "item_uncertain": True}
        ],
        "warp_ok": True,
        "item_conf_threshold": 0.85,
    }
    detail = CurationService(repo=_Repo(result)).get_detail(1)
    assert detail["pairs"][0]["uncertain"] is True
    assert detail["pairs"][0]["top5"] == [{"label": "무", "sim": 0.4}]


def test_pair_is_confident_when_flag_absent():
    """item_conf_threshold 도입 이전 잡 — 플래그가 없으면 확신으로 본다(하위호환)."""
    result = {"rows": [{"row_index": 0, "item_top5": []}], "warp_ok": True}
    detail = CurationService(repo=_Repo(result)).get_detail(1)
    assert detail["pairs"][0]["uncertain"] is False


def test_pair_is_confident_when_row_join_fails():
    """result_json에 해당 row_index가 없어도 배지를 잘못 띄우지 않는다."""
    detail = CurationService(repo=_Repo({"rows": [], "warp_ok": True})).get_detail(1)
    assert detail["pairs"][0]["uncertain"] is False
    assert detail["pairs"][0]["top5"] == []


def test_rows_join_by_row_index_not_array_position():
    """조인 키는 배열 위치가 아니라 row_index다.

    pair 순서(3, 7)와 rows 배열 순서(7, 3)를 어긋나게 두고 row_index도 비연속으로 잡는다 —
    0,1처럼 연속·동순서면 위치 조인과 키 조인이 같은 결과를 내 회귀를 못 잡는다. 오프셋
    조인은 검수 화면에서 엉뚱한 품목에 uncertain 배지를 띄우는 오검수다.
    """
    result = {
        "rows": [
            {"row_index": 7, "item_top5": [{"label": "칠", "sim": 0.9}], "item_uncertain": False},
            {"row_index": 3, "item_top5": [{"label": "삼", "sim": 0.4}], "item_uncertain": True},
        ],
        "warp_ok": True,
    }
    detail = CurationService(repo=_Repo(result, [_pair(1, 3), _pair(2, 7)])).get_detail(1)
    by_row = {p["row_index"]: p for p in detail["pairs"]}
    assert by_row[3]["uncertain"] is True
    assert by_row[3]["top5"] == [{"label": "삼", "sim": 0.4}]
    assert by_row[7]["uncertain"] is False
    assert by_row[7]["top5"] == [{"label": "칠", "sim": 0.9}]


# ── 외부 경계(ML 워커 result_json) 파손 내성 ───────────────────────────────
# 아래 3종은 모두 잡 상세 전체를 500으로 만들어 그 잡의 검수를 완전히 막는 경로다.
# 조인 실패를 이미 '빈 행'으로 닫아둔 것과 같은 fail-safe로 닫혀야 한다.


def test_detail_survives_null_result_json():
    """추론 미완/실패 잡(result_json IS NULL)도 검수 화면이 열려야 한다."""
    detail = CurationService(repo=_Repo(None)).get_detail(1)
    assert detail["warp_ok"] is False
    assert detail["pairs"][0]["uncertain"] is False
    assert detail["pairs"][0]["top5"] == []


def test_detail_survives_null_rows_key():
    """rows가 명시적 null이면 .get('rows', []) 기본값이 적용되지 않는다(TypeError 경로)."""
    detail = CurationService(repo=_Repo({"rows": None, "warp_ok": True})).get_detail(1)
    assert detail["pairs"][0]["uncertain"] is False
    assert detail["pairs"][0]["top5"] == []


def test_detail_survives_non_dict_row_elements():
    """rows 원소가 dict가 아니면 r.get(...)이 AttributeError를 낸다."""
    repo = _Repo({"rows": ["broken", None, 3], "warp_ok": True})
    detail = CurationService(repo=repo).get_detail(1)
    assert detail["pairs"][0]["uncertain"] is False
    assert detail["pairs"][0]["top5"] == []

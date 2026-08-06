"""CurationService 단위 테스트 — repository는 mock, DB 비의존."""

from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from app.core.errors import AppError
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
                "curation_reviewed_at": None,
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


# ── 정식 라벨 → 자동완성 사전 단방향 등록(ADR 0008, #40 spec §3.2) ──────────


def _sync_svc(repo, item_repo):
    """등록 트리거 검증용 서비스 — 트랜잭션은 nullcontext로 대체(DB 비의존)."""
    return CurationService(repo, item_repo, transaction=nullcontext)


def _registered(item_repo) -> list[str]:
    return [c.args[0] for c in item_repo.ensure_exists.call_args_list]


def test_mark_reviewed_registers_included_labels():
    repo = MagicMock()
    repo.job_exists.return_value = True
    repo.list_included_labels.return_value = ["휠", "중고"]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7)

    assert _registered(item_repo) == ["휠", "중고"]
    repo.list_included_labels.assert_called_once_with(7)


def test_mark_reviewed_skips_blank_labels():
    """빈 문자열·공백만인 canonical_label은 사전에 새지 않는다.

    확정 요청의 품목 name은 빈 문자열이 허용되고(app/schemas/ocr.py), ocr_correction이
    그 값을 그대로 canonical_label로 삼아 included 쌍을 만든다 — 실재하는 입력이다.
    """
    repo = MagicMock()
    repo.job_exists.return_value = True
    repo.list_included_labels.return_value = ["", "   ", "  배선수리  "]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7)

    assert _registered(item_repo) == ["배선수리"]  # strip 후 등록, 빈 값은 skip


def test_mark_reviewed_dedupes_repeated_labels_across_rows():
    """같은 라벨이 여러 행에 있어도 ensure_exists는 라벨당 한 번만 호출된다.

    반복 INSERT는 unique 인덱스 락을 매번 다시 잡는 락 위생 문제라 dedup한다.
    공백만 다른 변형(" 휠 ")도 같은 라벨이다 — list_included_labels는 공백을 그대로
    넘기므로(SQL은 NULL만 거른다), 원본 문자열 기준 dedup은 이 케이스를 놓쳐
    동일한 ensure_exists("휠")를 두 번 발행한다. 정규화 후에 dedup해야 한다.
    """
    repo = MagicMock()
    repo.job_exists.return_value = True
    repo.list_included_labels.return_value = ["휠", "중고", "휠", " 휠 "]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7)

    assert _registered(item_repo) == ["휠", "중고"]


def test_mark_reviewed_response_shape_is_unchanged():
    repo = MagicMock()
    repo.job_exists.return_value = True
    repo.list_included_labels.return_value = []
    result = _sync_svc(repo, MagicMock()).mark_reviewed(7)
    assert result == {"job_id": 7, "curation_reviewed": True}


def _patched(status, label):
    """patch_pair 경로용 repo mock — 갱신 후 find_pair가 돌려줄 상태를 고정한다."""
    repo = MagicMock()
    pair = {
        "id": 5,
        "crop_ref": "job-3/row-0",
        "job_id": 3,
        "row_index": 0,
        "draft_label": "중고타이어",
        "final_label": label,
        "canonical_label": label,
        "supply": 8000,
        "status": status,
        "exclusion_reason": None,
        "reviewed_at": None,
    }
    repo.find_pair.return_value = pair
    return repo


def test_patch_pair_releases_the_job_gate():
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"})

    repo.release_gate.assert_called_once_with(3)


def test_patch_pair_releases_gate_before_updating_pair():
    """락 순서 불변식(spec §4.2) — ocr_jobs(부모) 먼저, training_pairs(자식) 나중.

    mark_reviewed가 이미 부모부터 잠그므로, patch_pair가 반대 순서로 잠그면 두 경로
    사이에 순환 대기가 성립한다. 실 MySQL 2-커넥션 재현 테스트는 conftest의 테스트별
    TRUNCATE 격리와 맞지 않고 flaky하므로, 불변식을 **호출 순서**로 고정한다.
    """
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"})

    calls = [c[0] for c in repo.method_calls]
    assert calls.index("release_gate") < calls.index("update_pair")


def test_patch_pair_does_not_register_labels_even_if_a_reviewed_check_returns_true():
    """검수완료 잡의 라벨을 고쳐도 사전에 등록하지 않는다(spec §3.3 회귀 방어).

    이 우회 경로의 존재 이유는 "검수완료 버튼이 disabled라 등록 트리거를 다시 걸 수
    없다"였는데, #52가 그 전제를 없앴다(게이트가 해제되어 버튼이 재활성화된다).
    mark_reviewed를 단일 등록 지점으로 되돌린다 — 게이트가 풀린 상태에서 학습용 라벨만
    먼저 사전에 새는 모순을 막는다(ADR 0008).

    is_job_reviewed는 #52가 제거한 repository API다. 그런데도 True를 **명시적으로**
    심는 이유: 이 분기가 되살아나는 회귀를 MagicMock의 암묵적 truthy 반환에 기대면
    (본문에 아무것도 안 보인다) 나중에 기본값이 False인 mock으로 바뀔 때 이 테스트가
    이름과 반대되는 것을 조용히 단언하게 된다.
    """
    repo = _patched("included", "휠")
    repo.is_job_reviewed.return_value = True
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).patch_pair(5, {"canonical_label": "휠"})

    # _registered가 ensure_exists.call_args_list에서 파생되므로 assert_not_called와 같은 검사다.
    assert _registered(item_repo) == []


def test_patch_pair_response_shape_adds_job_gate():
    repo = _patched("included", "휠")
    result = _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"})
    assert set(result) == {
        "id",
        "crop_ref",
        "job_id",
        "row_index",
        "draft_label",
        "final_label",
        "canonical_label",
        "supply",
        "status",
        "exclusion_reason",
        "reviewed_at",
        "job_curation_reviewed",
    }
    assert result["canonical_label"] == "휠"
    # 해제는 무조건이므로(spec §3.4) 항상 False다.
    assert result["job_curation_reviewed"] is False


def test_patch_pair_404_when_pair_missing():
    """존재하지 않는 쌍은 트랜잭션에 들어가기 전에 404 — release_gate가 호출되지 않는다."""
    repo = MagicMock()
    repo.find_pair.return_value = None

    with pytest.raises(AppError) as ei:
        _sync_svc(repo, MagicMock()).patch_pair(999, {"status": "included"})

    # AppError 기반 타입만 보면 400/409로 바뀌어도 통과한다 — status를 고정한다.
    assert ei.value.status == 404
    repo.release_gate.assert_not_called()


# ---------------------------------------------------------------------------
# request_reprocess (spec §10)
# ---------------------------------------------------------------------------


def test_request_reprocess_requeues_a_done_job():
    repo = MagicMock()
    repo.find_job_for_update.return_value = {"id": 7, "status": "done"}

    result = _sync_svc(repo, MagicMock()).request_reprocess(7)

    repo.requeue_for_reprocess.assert_called_once_with(7)
    assert result == {"job_id": 7, "status": "pending"}


def test_request_reprocess_404_when_job_missing():
    repo = MagicMock()
    repo.find_job_for_update.return_value = None

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).request_reprocess(7)

    assert exc.value.status == 404
    repo.requeue_for_reprocess.assert_not_called()


def test_request_reprocess_409_when_job_is_not_done():
    repo = MagicMock()
    repo.find_job_for_update.return_value = {"id": 7, "status": "pending"}

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).request_reprocess(7)

    assert exc.value.status == 409
    repo.requeue_for_reprocess.assert_not_called()
    repo.update_pair.assert_not_called()

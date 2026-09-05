"""CurationService 단위 테스트 — repository는 mock, DB 비의존."""

from contextlib import contextmanager, nullcontext
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
    def __init__(self, result_json, pairs: list[dict] | None = None, *, status="done"):
        self._result_json = result_json
        self._pairs = [_pair(1, 0)] if pairs is None else pairs
        self._status = status

    def find_job_detail(self, job_id: int) -> dict:
        return {
            "job": {
                "id": job_id,
                "invoice_id": 10,
                "status": self._status,
                "curation_reviewed": 0,
                "curation_reviewed_at": None,
                "created_at": "2026-07-28T09:00:00",
                "job_token": "1000",
                "result_json": self._result_json,
            },
            "pairs": self._pairs,
        }


def test_detail_exposes_job_status():
    # 서비스는 잡 상태를 가공 없이 통과시킨다 — 화면이 pending/running/failed를 각각
    # 다른 문구로 갈라 쓰므로 done 여부로 접으면 안 된다.
    detail = CurationService(_Repo({"rows": []}, status="pending")).get_detail(1)
    assert detail["status"] == "pending"


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


def _reviewable(status="done", token="1000"):
    """mark_reviewed 경로용 repo mock.

    MagicMock의 기본 반환은 dict가 아니라 Mock이라 find_job_for_update를 세우지 않으면
    job["status"]가 TypeError가 된다.
    """
    repo = MagicMock()
    repo.find_job_for_update.return_value = {"id": 7, "status": status}
    repo.get_job_token.return_value = token
    repo.list_included_labels.return_value = []
    return repo


def test_mark_reviewed_registers_included_labels():
    repo = _reviewable()
    repo.list_included_labels.return_value = ["휠", "중고"]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7, "1000")

    assert _registered(item_repo) == ["휠", "중고"]
    repo.list_included_labels.assert_called_once_with(7)


def test_mark_reviewed_skips_blank_labels():
    """빈 문자열·공백만인 canonical_label은 사전에 새지 않는다.

    확정 요청의 품목 name은 빈 문자열이 허용되고(app/schemas/ocr.py), ocr_correction이
    그 값을 그대로 canonical_label로 삼아 included 쌍을 만든다 — 실재하는 입력이다.
    """
    repo = _reviewable()
    repo.list_included_labels.return_value = ["", "   ", "  배선수리  "]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7, "1000")

    assert _registered(item_repo) == ["배선수리"]  # strip 후 등록, 빈 값은 skip


def test_mark_reviewed_dedupes_repeated_labels_across_rows():
    """같은 라벨이 여러 행에 있어도 ensure_exists는 라벨당 한 번만 호출된다.

    반복 INSERT는 unique 인덱스 락을 매번 다시 잡는 락 위생 문제라 dedup한다.
    공백만 다른 변형(" 휠 ")도 같은 라벨이다 — list_included_labels는 공백을 그대로
    넘기므로(SQL은 NULL만 거른다), 원본 문자열 기준 dedup은 이 케이스를 놓쳐
    동일한 ensure_exists("휠")를 두 번 발행한다. 정규화 후에 dedup해야 한다.
    """
    repo = _reviewable()
    repo.list_included_labels.return_value = ["휠", "중고", "휠", " 휠 "]
    item_repo = MagicMock()

    _sync_svc(repo, item_repo).mark_reviewed(7, "1000")

    assert _registered(item_repo) == ["휠", "중고"]


def test_mark_reviewed_response_shape_is_unchanged():
    repo = _reviewable()
    result = _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")
    assert result == {"job_id": 7, "curation_reviewed": True}


_PAIR_ID = 5
_JOB_ID = 3


def _patched(status, label, job_status="done"):
    """patch_pair 경로용 repo mock — 갱신 후 find_pair가 돌려줄 상태를 고정한다.

    MagicMock의 기본 반환은 dict가 아니라 Mock이라 find_job_for_update를 세우지 않으면
    잡 상태 가드가 Mock != "done"으로 걸린다(_reviewable과 같은 이유).

    **인자 의존 stub인 것이 계약이다(#94).** 인자와 무관한 고정값을 돌려주면 서비스가
    엉뚱한 id로 조회·잠금해도 전량 GREEN이라, "요청된 쌍의 소유 잡을 잠근다"는 회귀가
    통째로 미검출된다. 토큰도 두 번의 읽기(② 대조 · 갱신 후 반환)를 서로 다른 값으로
    돌려줘야 어느 쪽을 비교하고 어느 쪽을 반환하는지가 단언으로 갈린다.
    """
    repo = MagicMock()
    repo.find_job_for_update.side_effect = lambda job_id: (
        {"id": _JOB_ID, "status": job_status} if job_id == _JOB_ID else None
    )
    pair = {
        "id": _PAIR_ID,
        "crop_ref": "job-3/row-0",
        "job_id": _JOB_ID,
        "row_index": 0,
        "draft_label": "중고타이어",
        "final_label": label,
        "canonical_label": label,
        "supply": 8000,
        "status": status,
        "exclusion_reason": None,
        "reviewed_at": None,
    }
    repo.find_pair.side_effect = lambda pair_id: pair if pair_id == _PAIR_ID else None
    repo.get_job_token.side_effect = ["1000", "1001"]
    return repo


def test_patch_pair_releases_the_job_gate():
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    repo.release_gate.assert_called_once_with(3)


def test_patch_pair_releases_gate_before_updating_pair():
    """락 순서 불변식(spec §4.2) — ocr_jobs(부모) 먼저, training_pairs(자식) 나중.

    mark_reviewed가 이미 부모부터 잠그므로, patch_pair가 반대 순서로 잠그면 두 경로
    사이에 순환 대기가 성립한다. 실 MySQL 2-커넥션 재현 테스트는 conftest의 테스트별
    TRUNCATE 격리와 맞지 않고 flaky하므로, 불변식을 **호출 순서**로 고정한다.
    """
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

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

    _sync_svc(repo, item_repo).patch_pair(5, {"canonical_label": "휠"}, "1000")

    # _registered가 ensure_exists.call_args_list에서 파생되므로 assert_not_called와 같은 검사다.
    assert _registered(item_repo) == []


def test_patch_pair_response_shape_adds_job_gate():
    repo = _patched("included", "휠")
    result = _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")
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
        "job_token",
    }
    assert result["canonical_label"] == "휠"
    # 해제는 무조건이므로(spec §3.4) 항상 False다.
    assert result["job_curation_reviewed"] is False


def test_patch_pair_404_when_pair_missing():
    """존재하지 않는 쌍은 트랜잭션에 들어가기 전에 404 — release_gate가 호출되지 않는다."""
    repo = MagicMock()
    repo.find_pair.return_value = None

    with pytest.raises(AppError) as ei:
        _sync_svc(repo, MagicMock()).patch_pair(999, {"status": "included"}, "1000")

    # AppError 기반 타입만 보면 400/409로 바뀌어도 통과한다 — status를 고정한다.
    assert ei.value.status == 404
    repo.release_gate.assert_not_called()
    repo.update_pair.assert_not_called()


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
    assert "처리 중" in exc.value.message, "failed까지 허용된 지금 '추론이 끝난 잡만'은 거짓말이다"
    repo.requeue_for_reprocess.assert_not_called()


def test_request_reprocess_requeues_a_failed_job():
    """failed 잡의 유일한 API 복구 경로다(이슈 #93) — 이전에는 어디에도 없었다.

    안전성은 워커 판별자 축소(#91)가 근거다: 에러 JSON만 남은 실패 잡은 rows가 없어
    신규로, 초안 보존 실패 잡(#92·#88)은 rows가 남아 재처리로 스스로 재분류된다.
    """
    repo = MagicMock()
    repo.find_job_for_update.return_value = {"id": 7, "status": "failed"}

    result = _sync_svc(repo, MagicMock()).request_reprocess(7)

    repo.requeue_for_reprocess.assert_called_once_with(7)
    assert result == {"job_id": 7, "status": "pending"}


# ---------------------------------------------------------------------------
# get_detail — 미결 쌍은 새 행과 조인되지 않는다 (spec §6-1)
# ---------------------------------------------------------------------------


def _detail_repo(pairs):
    repo = MagicMock()
    repo.find_job_detail.return_value = {
        "job": {
            "id": 42,
            "invoice_id": None,
            "status": "done",
            "curation_reviewed": 0,
            "curation_reviewed_at": None,
            "created_at": "2026-08-06T00:00:00",
            "job_token": "1000",
            "result_json": {
                "rows": [
                    {
                        "row_index": 0,
                        "item_top5": [{"label": "무", "sim": 0.9}],
                        "item_uncertain": True,
                    },
                    {
                        "row_index": 1,
                        "item_top5": [{"label": "파", "sim": 0.5}],
                        "item_uncertain": True,
                    },
                ]
            },
        },
        "pairs": pairs,
    }
    return repo


def _orphan_pair(pair_id, crop_ref, row_index):
    return {
        "id": pair_id,
        "crop_ref": crop_ref,
        "row_index": row_index,
        "draft_label": None,
        "final_label": "무",
        "canonical_label": "무",
        "supply": 3000,
        "status": "included",
        "exclusion_reason": None,
        "reviewed_at": None,
    }


def test_get_detail_marks_row_shaped_crop_refs_available():
    repo = _detail_repo([_orphan_pair(1, "job-42/row-0", 0)])

    pair = _sync_svc(repo, MagicMock()).get_detail(42)["pairs"][0]

    assert pair["crop_available"] is True
    assert pair["top5"] == [{"label": "무", "sim": 0.9}]


def test_get_detail_never_joins_orphaned_pairs_to_new_rows():
    """옛 row-0 미결 라벨 옆에 전혀 다른 줄의 crop·top5가 붙는 것을 막는다(§6-1)."""
    repo = _detail_repo([_orphan_pair(1, "job-42/orphan-1", 0)])

    pair = _sync_svc(repo, MagicMock()).get_detail(42)["pairs"][0]

    assert pair["crop_available"] is False
    assert pair["top5"] == []
    assert pair["uncertain"] is False


def test_get_detail_keeps_row_index_untouched_for_orphans():
    """row_index 값 자체는 손대지 않는다 — 진실은 crop_ref가 이미 갖고 있다(§6-1).

    row_index 단독 단언은 조인 봉쇄 게이트를 지워도 참이 되는 무기력 검증이라(row_index
    0은 게이트 유무와 무관하게 그대로 통과한다), top5·crop_available과 함께 단언해
    게이트가 실제로 살아 있는지를 고정한다.
    """
    repo = _detail_repo([_orphan_pair(1, "job-42/orphan-1", 0)])

    pair = _sync_svc(repo, MagicMock()).get_detail(42)["pairs"][0]

    assert (pair["row_index"], pair["top5"], pair["crop_available"]) == (0, [], False)


def test_get_detail_sorts_orphans_after_real_rows():
    """ORDER BY row_index로는 미결이 실제 행 사이에 끼어 읽는 사람을 헷갈리게 한다."""
    repo = _detail_repo(
        [
            _orphan_pair(1, "job-42/orphan-1", 0),
            _orphan_pair(2, "job-42/row-0", 0),
            _orphan_pair(3, "job-42/row-1", 1),
        ]
    )

    ids = [p["id"] for p in _sync_svc(repo, MagicMock()).get_detail(42)["pairs"]]

    assert ids == [2, 3, 1]


def test_get_detail_does_not_use_exclusion_reason_as_the_marker():
    """사람 배제가 사유를 NULL로 지우므로 exclusion_reason은 안정적 표식이 아니다(§6-1)."""
    orphan = {
        **_orphan_pair(1, "job-42/orphan-1", 0),
        "status": "excluded",
        "exclusion_reason": None,
    }
    repo = _detail_repo([orphan])

    assert _sync_svc(repo, MagicMock()).get_detail(42)["pairs"][0]["crop_available"] is False


# ---------------------------------------------------------------------------
# 낙관적 잠금 (spec §12)
# ---------------------------------------------------------------------------


def test_patch_pair_rejects_a_job_that_is_not_done_before_comparing_tokens():
    """재처리 큐에 든 잡은 토큰이 맞아도 거부한다 — 토큰만으로는 이 경로를 못 막는다.

    409 안내대로 새로고침하면 pending 잡의 유효한 새 토큰이 손에 들어와 같은 PATCH가
    통과하기 때문이다. 상태 가드를 지우면 이 단언이 RED가 된다.
    """
    repo = _patched("included", "휠", job_status="pending")

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    assert exc.value.status == 409
    repo.update_pair.assert_not_called()


def test_patch_pair_rejects_a_stale_token_with_409():
    """재처리 이전 화면을 열어둔 사용자가 옛 그림을 근거로 새 쌍을 고치는 것을 막는다."""
    repo = _patched("included", "휠")
    repo.get_job_token.side_effect = ["2000"]

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    assert exc.value.status == 409
    assert exc.value.code == "CONFLICT"
    repo.update_pair.assert_not_called()
    repo.release_gate.assert_not_called()


def test_patch_pair_reads_the_token_before_touching_the_pair():
    """락 순서 — 부모(ocr_jobs) 조회·게이트 해제가 자식(training_pairs) 쓰기보다 앞선다.

    두 지표의 index 비교만으로는 사이에 낀 순서 변경(게이트 해제와 쌍 갱신의 자리바꿈,
    제3 호출의 삽입)이 드러나지 않는다. 시퀀스 전량을 못 박는다. 반환 토큰이 **갱신 후**
    읽기라는 축은 test_patch_pair_returns_the_refreshed_token이 이미 소유하므로 여기서
    겹치지 않는다.
    """
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    assert [c[0] for c in repo.method_calls] == [
        "find_pair",
        "find_job_for_update",
        "get_job_token",
        "release_gate",
        "update_pair",
        "find_pair",
        "get_job_token",
    ]


def test_patch_pair_returns_the_refreshed_token():
    """프론트가 연속 편집을 이어갈 수 있도록 갱신된 토큰을 돌려준다."""
    repo = _patched("included", "휠")
    repo.get_job_token.side_effect = ["1000", "1001"]

    result = _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    assert result["job_token"] == "1001"


def test_patch_pair_locks_the_job_that_owns_the_requested_pair():
    """잠그고 고치는 대상은 요청된 쌍과 그 쌍의 소유 잡이다 — id가 섞이면 남의 잡을 잠근다.

    stub이 인자 무관 고정값이면 find_job_for_update(pair_id) 같은 회귀가 그대로 통과한다.
    """
    repo = _patched("included", "휠")

    _sync_svc(repo, MagicMock()).patch_pair(5, {"canonical_label": "휠"}, "1000")

    repo.find_pair.assert_any_call(5)
    repo.find_job_for_update.assert_called_once_with(3)
    repo.release_gate.assert_called_once_with(3)
    repo.update_pair.assert_called_once_with(5, {"canonical_label": "휠"})
    assert [c.args for c in repo.get_job_token.call_args_list] == [(3,), (3,)]


def test_mark_reviewed_rejects_a_stale_token_with_409():
    """재처리 이전에 열어둔 화면의 검수 완료가 새 미결 쌍을 덮는 것을 막는다.

    통과시키면 미결 쌍의 reviewed_at이 찍혀 검수 큐에서 사라지고(복구는 수동 SQL),
    --reembed-job 가드가 통과되어 그 잡이 재검수 없이 재임베딩된다(§7·§11-1).
    """
    repo = _reviewable(token="2000")

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")

    assert exc.value.status == 409
    assert exc.value.code == "CONFLICT"
    repo.mark_reviewed.assert_not_called()


def test_mark_reviewed_rejects_a_job_that_is_not_done_with_409():
    """워커가 곧 덮어쓸 잡의 검수 완료는 의미가 없다 — reprocess와 같은 규칙이다."""
    repo = _reviewable(status="pending")

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")

    assert exc.value.status == 409
    repo.mark_reviewed.assert_not_called()


def test_mark_reviewed_409_names_the_failure_for_a_failed_job():
    """failed 잡에 '처리가 끝난 뒤 다시 시도하세요'는 사실과 다르다 — 영영 끝나지 않는다(#93).

    상태별로 메시지를 갈라 실패 사실과 복구 경로(재처리)를 알려야 사람이 기다리다 포기하는
    대신 행동할 수 있다. 409라는 계약 자체는 그대로다.
    """
    repo = _reviewable(status="failed")

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")

    assert exc.value.status == 409
    assert "실패" in exc.value.message
    assert "끝난 뒤" not in exc.value.message
    repo.mark_reviewed.assert_not_called()


def test_mark_reviewed_404_when_job_missing():
    """존재 확인은 토큰 대조보다 앞선다 — 없는 잡은 409가 아니라 404다."""
    repo = _reviewable()
    repo.find_job_for_update.return_value = None

    with pytest.raises(AppError) as exc:
        _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")

    assert exc.value.status == 404
    repo.mark_reviewed.assert_not_called()


def test_mark_reviewed_locks_the_job_before_stamping_pairs():
    """락 순서 — 상태·토큰 조회(FOR UPDATE)가 자식 쓰기보다 앞선다."""
    repo = _reviewable()

    _sync_svc(repo, MagicMock()).mark_reviewed(7, "1000")

    calls = [c[0] for c in repo.method_calls]
    assert calls.index("find_job_for_update") < calls.index("mark_reviewed")
    assert calls.index("get_job_token") < calls.index("mark_reviewed")


# ── 트랜잭션 경계 불변식(#84) ─────────────────────────────────────────────

_TRACKED_METHODS = (
    "find_job_for_update",
    "get_job_token",
    "release_gate",
    "update_pair",
    "find_pair",
    "mark_reviewed",
    "list_included_labels",
)


def _recorder(timeline, name, method):
    """호출을 타임라인에 남기되 mock에 이미 세워진 스텁을 그대로 태우는 side_effect.

    side_effect를 고정값으로 갈아끼우면 _patched의 인자 의존 stub과 토큰 2연값이 지워져,
    find_job_for_update가 dict 대신 미설정 Mock을 돌려주고 잡 상태 가드가 409로 걸린다 —
    기록만 얹고 반환은 원래 스텁(side_effect 우선, 없으면 return_value)에 위임한다.
    """
    stub = method.side_effect

    if stub is None:

        def _inner(*args, **kwargs):
            return method.return_value

    elif callable(stub):
        _inner = stub
    else:
        values = iter(stub)

        def _inner(*args, **kwargs):
            return next(values)

    def _call(*args, **kwargs):
        timeline.append(name)
        return _inner(*args, **kwargs)

    return _call


def _tracking_transaction(timeline):
    """enter/exit 시점을 repo 호출과 같은 타임라인에 남기는 트랜잭션 스텁."""

    @contextmanager
    def _transaction():
        timeline.append("enter")
        try:
            yield None
        finally:
            timeline.append("exit")

    return _transaction


def _tracked_svc(repo, timeline, item_repo=None):
    """repo 호출과 트랜잭션 경계를 한 타임라인에 기록하는 서비스."""
    for name in _TRACKED_METHODS:
        method = getattr(repo, name)
        method.side_effect = _recorder(timeline, name, method)
    return CurationService(
        repo, item_repo or MagicMock(), transaction=_tracking_transaction(timeline)
    )


def test_patch_pair_reads_the_job_token_inside_the_transaction():
    """토큰 조회 2회(② 대조 · 재발급)가 모두 enter와 exit **사이**에 있어야 한다(#84).

    enter 선행만 단언하면 호출을 with 블록 뒤로 옮겨도 통과한다(enter는 이미 발생했다) —
    exit 이전까지 함께 고정해야 경계가 양쪽으로 닫힌다. 조회와 쓰기 사이가 벌어지면
    낙관적 잠금이 무의미해진다.
    """
    timeline = []
    repo = _patched("included", "휠")

    _tracked_svc(repo, timeline).patch_pair(5, {"canonical_label": "휠"}, "1000")

    reads = [i for i, event in enumerate(timeline) if event == "get_job_token"]
    assert len(reads) == 2
    assert timeline.index("enter") < reads[0]
    assert reads[-1] < timeline.index("exit")


def test_mark_reviewed_reads_the_job_token_inside_the_transaction():
    """검수 완료의 세대 대조도 같은 트랜잭션 안이어야 한다 — 한쪽만 막으면 방어가 반쪽이다."""
    timeline = []
    repo = _reviewable()

    _tracked_svc(repo, timeline).mark_reviewed(7, "1000")

    reads = [i for i, event in enumerate(timeline) if event == "get_job_token"]
    assert len(reads) == 1
    assert timeline.index("enter") < reads[0] < timeline.index("exit")


# ---------------------------------------------------------------------------
# list_jobs — row_delta 관통 (S1)
# ---------------------------------------------------------------------------


class _ListRepo:
    """list_jobs만 흉내내는 fake — 정규화 계층을 실 MySQL 없이 고정한다."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.calls: list[tuple] = []

    def list_jobs(self, limit, offset, *, row_delta=False):
        self.calls.append((limit, offset, row_delta))
        return self._rows, len(self._rows)


def _summary_row(**over) -> dict:
    base = {
        "job_id": 1,
        "invoice_id": None,
        "curation_reviewed": 0,
        "curation_reviewed_at": None,
        "pair_count": 1,
        "unreviewed_count": 1,
        "rows_added": None,
        "rows_dropped": None,
        "created_at": "2026-09-01T09:00:00",
    }
    return {**base, **over}


def test_list_jobs_keeps_unobserved_row_delta_as_none():
    # 0으로 접으면 "관측 없음"이 "증감 없음"으로 위장한다.
    jobs, _total = CurationService(_ListRepo([_summary_row()])).list_jobs(1, 20)
    assert jobs[0]["rows_added"] is None
    assert jobs[0]["rows_dropped"] is None


def test_list_jobs_keeps_observed_zero_as_zero():
    repo = _ListRepo([_summary_row(rows_added=0, rows_dropped=0)])
    jobs, _total = CurationService(repo).list_jobs(1, 20)
    assert jobs[0]["rows_added"] == 0
    assert jobs[0]["rows_dropped"] == 0


def test_list_jobs_forwards_row_delta_and_offset_to_repository():
    # limit != offset인 조합 — 같으면 위치인자 뒤바뀜(offset, limit)을 잡지 못한다.
    repo = _ListRepo([])
    CurationService(repo).list_jobs(3, 10, row_delta=True)
    assert repo.calls == [(10, 20, True)]

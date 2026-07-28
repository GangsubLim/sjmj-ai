"""CurationService 단위 테스트 — repository는 mock, DB 비의존."""

from app.services.curation_service import CurationService


class _Repo:
    def __init__(self, result_json: dict):
        self._result_json = result_json

    def find_job_detail(self, job_id: int) -> dict:
        return {
            "job": {
                "id": job_id,
                "invoice_id": 10,
                "curation_reviewed": 0,
                "created_at": "2026-07-28T09:00:00",
                "result_json": self._result_json,
            },
            "pairs": [
                {
                    "id": 1,
                    "crop_ref": "job-1/row-0",
                    "row_index": 0,
                    "draft_label": "무우",
                    "final_label": "무",
                    "canonical_label": "무",
                    "supply": 8000,
                    "status": "included",
                    "reviewed_at": None,
                }
            ],
        }


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

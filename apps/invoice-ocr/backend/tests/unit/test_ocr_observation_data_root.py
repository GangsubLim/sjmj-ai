"""데이터 루트 오설정에서도 관측 목록이 살아남는지 고정한다.

관측 목록은 읽기 전용 진단 화면이다(ADR 0009). 하필 SJMJ_DATA_DIR 오설정을 진단하려고
여는 화면이 500으로 죽으면, DB만으로 판정되는 pending/running/failed 행조차 못 그린다.
"""

import pytest

from app.services.ocr_service import OcrService


class _FakeUnconfirmedRepo:
    """list_unconfirmed만 답하는 fake — 실 DB 없이 정규화 로직만 검증한다."""

    def __init__(self, rows):
        self.rows = rows

    def list_unconfirmed(self, limit, offset):
        return self.rows, len(self.rows)


def _raw(job_id, **over):
    base = {
        "job_id": job_id,
        "status": "done",
        "created_at": "2026-08-01T09:00:00",
        "rows_type": "ARRAY",
        "row_count": 3,
        "warp_ok": "true",
        "error": None,
    }
    return {**base, **over}


@pytest.mark.parametrize("data_dir", [None, "/nonexistent/sjmj-data-dir"])
def test_list_unconfirmed_survives_broken_data_root(monkeypatch, data_dir):
    monkeypatch.delenv("SJMJ_DATA_DIR", raising=False)
    if data_dir is not None:
        monkeypatch.setenv("SJMJ_DATA_DIR", data_dir)
    rows = [_raw(1, status="failed", error="boom"), _raw(2), _raw(3, status="pending")]
    service = OcrService(repo=_FakeUnconfirmedRepo(rows))

    jobs, total = service.list_unconfirmed(1, 20)

    assert total == 3
    # DB만으로 판정되는 배지는 데이터 볼륨과 무관하게 그대로 나와야 한다.
    assert [j["observation_status"] for j in jobs] == ["failed", "unconfirmed", "pending"]


def test_unobservable_warped_file_degrades_to_no_warp(monkeypatch):
    """warped.png를 볼 수 없으면 강등이 아니라 '워프 산출 없음'으로 닫는다.

    NO_WARP는 이미 "볼 워프 산출이 없다"까지만 말하는 배지다(ocr_observation 모듈 docstring
    — 저장 실패·사후 유실도 같은 관측으로 본다). 관측 불가도 같은 부류로 흡수된다.
    """
    monkeypatch.delenv("SJMJ_DATA_DIR", raising=False)
    service = OcrService(repo=_FakeUnconfirmedRepo([_raw(9, warp_ok="false")]))

    jobs, _ = service.list_unconfirmed(1, 20)

    assert jobs[0]["observation_status"] == "no_warp"

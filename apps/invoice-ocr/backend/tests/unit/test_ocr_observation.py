import pytest

from app.services.ocr_observation import OBSERVATION_STATUSES, derive_observation_status


# 배지 8종 — spec "관측 상태" 표를 그대로 옮긴 것.
@pytest.mark.parametrize(
    "status,warp_ok,rows_type,row_count,has_warped,expected",
    [
        # 대기 / 처리중 / 실패 — status가 그대로 배지
        ("pending", None, None, None, False, "pending"),
        ("running", None, None, None, False, "running"),
        ("failed", None, None, None, False, "failed"),
        # 결과 없음 — done인데 warp_ok가 boolean이 아님(계약 위반)
        ("done", None, "ARRAY", 3, True, "no_result"),
        # 워프 없음 — warp_ok=false + warped.png 부재
        ("done", "false", "ARRAY", 0, False, "no_warp"),
        # 강등 — warp_ok=false + warped.png 존재
        ("done", "false", "ARRAY", 0, True, "demoted"),
        # 행 미검출 — warp_ok=true + rows 길이 0
        ("done", "true", "ARRAY", 0, True, "no_rows"),
        # 미확정 — warp_ok=true + rows 길이 > 0
        ("done", "true", "ARRAY", 12, True, "unconfirmed"),
    ],
)
def test_derives_eight_badges(status, warp_ok, rows_type, row_count, has_warped, expected):
    assert (
        derive_observation_status(
            status=status,
            warp_ok=warp_ok,
            rows_type=rows_type,
            row_count=row_count,
            has_warped=has_warped,
        )
        == expected
    )


# warp_ok="true" 분기는 has_warped에 의존하지 않는다. warped.png는 false 분기에서만
# 강등/워프 없음을 가르는 신호다(ocr_observation 모듈 docstring). 위 8종 표는 true 분기를
# has_warped=True로만 덮으므로, 이 독립성을 따로 고정하지 않으면 rows 검사 앞에
# `if not has_warped: return NO_WARP`을 넣는 회귀 — 정상 잡의 무더기 오강등 — 이 새어 나간다.
@pytest.mark.parametrize("row_count,expected", [(0, "no_rows"), (12, "unconfirmed")])
@pytest.mark.parametrize("has_warped", [True, False])
def test_true_warp_ok_branch_ignores_has_warped(row_count, expected, has_warped):
    assert (
        derive_observation_status(
            status="done",
            warp_ok="true",
            rows_type="ARRAY",
            row_count=row_count,
            has_warped=has_warped,
        )
        == expected
    )


# 계약 위반 입력 — 전부 no_result로 닫혀야 한다(정상으로 보이지 않게).
@pytest.mark.parametrize(
    "warp_ok",
    [None, "1", "0", "True", "TRUE", "null", "", True, False, 1],
)
def test_non_boolean_warp_ok_closes_to_no_result(warp_ok):
    assert (
        derive_observation_status(
            status="done", warp_ok=warp_ok, rows_type="ARRAY", row_count=3, has_warped=True
        )
        == "no_result"
    )


@pytest.mark.parametrize("rows_type", [None, "NULL", "OBJECT", "STRING", "INTEGER"])
def test_non_array_rows_type_closes_to_no_result(rows_type):
    # warp_ok=true여도 rows가 배열이 아니면 row_count를 신뢰하지 않는다.
    assert (
        derive_observation_status(
            status="done", warp_ok="true", rows_type=rows_type, row_count=3, has_warped=True
        )
        == "no_result"
    )


# A8: warp_ok가 유효한 "false"면 rows가 깨져 있어도 warp 분기가 이긴다(no_result가 아니다).
@pytest.mark.parametrize("rows_type,row_count", [(None, None), ("NULL", 1), ("OBJECT", 1)])
def test_valid_false_warp_ok_wins_over_broken_rows(rows_type, row_count):
    assert (
        derive_observation_status(
            status="done",
            warp_ok="false",
            rows_type=rows_type,
            row_count=row_count,
            has_warped=True,
        )
        == "demoted"
    )
    assert (
        derive_observation_status(
            status="done",
            warp_ok="false",
            rows_type=rows_type,
            row_count=row_count,
            has_warped=False,
        )
        == "no_warp"
    )


def test_rows_null_with_row_count_one_closes_to_no_result():
    """MySQL 실측 조합 고정 — rows: null은 rows_type='NULL' + row_count=1을 준다.

    row_count만 보면 "1행 검출된 미확정 잡"과 구별되지 않는다. rows_type 게이트가
    row_count보다 **먼저** 판정해야만 이 오분류가 막힌다.
    """
    assert (
        derive_observation_status(
            status="done", warp_ok="true", rows_type="NULL", row_count=1, has_warped=True
        )
        == "no_result"
    )


def test_array_rows_type_with_null_row_count_closes_to_no_result():
    # JSON_TYPE과 JSON_LENGTH가 어긋난 방어적 경우.
    assert (
        derive_observation_status(
            status="done", warp_ok="true", rows_type="ARRAY", row_count=None, has_warped=True
        )
        == "no_result"
    )


# A1: status 화이트리스트 — 표에 없는 status는 no_result로 닫는다.
@pytest.mark.parametrize("status", [None, "", "queued", "DONE", "cancelled"])
def test_unknown_status_closes_to_no_result(status):
    assert (
        derive_observation_status(
            status=status, warp_ok="true", rows_type="ARRAY", row_count=3, has_warped=True
        )
        == "no_result"
    )


def test_observation_statuses_is_exactly_the_eight_spec_badges():
    """배지 어휘를 spec 표와 철자 단위로 고정한다 — 코드 문자열 자체가 계약이다(프론트가 분기).

    아래 전수 테스트는 "8종에 속한다"만 보므로 어휘가 9종으로 늘거나 하나가 빠져도 통과한다.
    개수와 철자는 여기서만 못 박힌다.
    """
    assert set(OBSERVATION_STATUSES) == {
        "pending",
        "running",
        "failed",
        "no_result",
        "no_warp",
        "demoted",
        "no_rows",
        "unconfirmed",
    }
    assert len(OBSERVATION_STATUSES) == 8


def test_every_input_combination_closes_to_one_of_eight():
    statuses = [None, "", "pending", "running", "failed", "done", "queued"]
    warp_oks = [None, "true", "false", "1", "null", True]
    rows_types = [None, "ARRAY", "NULL", "OBJECT"]
    row_counts = [None, 0, 1, 99]
    for status in statuses:
        for warp_ok in warp_oks:
            for rows_type in rows_types:
                for row_count in row_counts:
                    for has_warped in (True, False):
                        got = derive_observation_status(
                            status=status,
                            warp_ok=warp_ok,
                            rows_type=rows_type,
                            row_count=row_count,
                            has_warped=has_warped,
                        )
                        assert got in OBSERVATION_STATUSES, (
                            status,
                            warp_ok,
                            rows_type,
                            row_count,
                            has_warped,
                            got,
                        )

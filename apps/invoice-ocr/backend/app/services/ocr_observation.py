"""처리 관측 배지 판정 — 순수함수.

읽기 전용 관측이며 게이트가 아니다(ADR 0009). 게이트 지표를 쓰지 않고 `warped.png`
파일 존재 여부 자체를 진단 신호로 삼는다 — 쿼드 미검출 경로는 워커가 이 파일을 쓰기
전에 리턴하므로(ml/handwriting/infer_job.py:184의 imwrite), 파일이 있으면 게이트 경로를
지난 것이 확실하다. 이 인과 추론은 "재처리가 미구현"이라는 사실에 의존한다 — 재처리가
도입되면 이전 실행이 남긴 stale warped.png가 추론을 깨므로 DEMOTED 배지를 재검토해야 한다.

배지 이름은 관측된 사실까지만 말한다. NO_WARP을 "전표 미검출"이라 부르지 않는 이유는
imwrite가 반환값을 확인하지 않아 저장 실패·사후 유실도 같은 관측으로 보이기 때문이다.
"""

# status 컬럼값이 그대로 배지가 되는 3종(추론 미완 또는 예외).
OBSERVATION_PENDING = "pending"
OBSERVATION_RUNNING = "running"
OBSERVATION_FAILED = "failed"
# result_json이 계약을 벗어나 판정 불가 — fail-safe.
OBSERVATION_NO_RESULT = "no_result"
# 볼 워프 산출이 없다(원인 판단은 운영자가 원본 사진을 보고 한다).
OBSERVATION_NO_WARP = "no_warp"
# 워프는 했는데 격자와 안 맞아 강등됐다.
OBSERVATION_DEMOTED = "demoted"
# 격자는 맞았는데 행이 안 잡혔다.
OBSERVATION_NO_ROWS = "no_rows"
# 기계는 성공, 사람이 확정 안 함. 뒷문장("확정 안 함")은 입력이 아니라 호출자 계약이다 —
# derive_observation_status의 호출 계약 항목 참조.
OBSERVATION_UNCONFIRMED = "unconfirmed"

OBSERVATION_STATUSES = frozenset(
    {
        OBSERVATION_PENDING,
        OBSERVATION_RUNNING,
        OBSERVATION_FAILED,
        OBSERVATION_NO_RESULT,
        OBSERVATION_NO_WARP,
        OBSERVATION_DEMOTED,
        OBSERVATION_NO_ROWS,
        OBSERVATION_UNCONFIRMED,
    }
)

_DONE = "done"
# status가 done이 아닐 때 그 값을 그대로 배지로 쓸 수 있는 화이트리스트.
# ocr_jobs.status는 enum이 아닌 VARCHAR(20)이라 임의 문자열이 들어올 수 있다 —
# 화이트리스트가 없으면 표에 없는 값이 배지로 새어 나간다.
_PASSTHROUGH = frozenset({OBSERVATION_PENDING, OBSERVATION_RUNNING, OBSERVATION_FAILED})
_WARP_OK_TRUE = "true"
_WARP_OK_FALSE = "false"
_ROWS_TYPE_ARRAY = "ARRAY"


def derive_observation_status(
    *,
    status: str | None,
    warp_ok: str | None,
    rows_type: str | None,
    row_count: int | None,
    has_warped: bool,
) -> str:
    """관측 상태 배지를 판정한다. 어떤 입력 조합도 OBSERVATION_STATUSES 안으로 닫힌다.

    호출 계약: 미확정 잡만 넘긴다. 확정 여부는 인자에 없고 여집합 쿼리
    (ocr_repository._UNCONFIRMED_WHERE)가 이미 걸러준 전제로 UNCONFIRMED를 낸다 —
    확정된 잡을 넘기면 '미확정' 배지가 조용히 붙는다. 다른 경로에서 재사용하려면
    그 경로도 같은 여집합을 통과시키거나, 이 함수 밖에서 확정 잡을 먼저 걷어내야 한다.
    (확정 여부를 인자로 받지 않는 이유: 배지 결정표를 warp/rows 신호만으로 닫아 두기 위함.)

    Args:
        status: ocr_jobs.status 값(신뢰할 수 없는 임의 문자열일 수 있다).
        warp_ok: JSON_UNQUOTE(JSON_EXTRACT(result_json,'$.warp_ok')) — "true"/"false" 또는 그 외.
        rows_type: JSON_TYPE(JSON_EXTRACT(result_json,'$.rows')) — "ARRAY"일 때만 신뢰한다.
        row_count: JSON_LENGTH(result_json,'$.rows').
        has_warped: crop_dir(job_id)/warped.png 존재 여부.

    Returns:
        OBSERVATION_STATUSES 중 하나.
    """
    if status != _DONE:
        return status if status in _PASSTHROUGH else OBSERVATION_NO_RESULT
    # warp_ok가 boolean이 아니면(부재·NULL·다른 타입) 판정 불가로 닫는다 —
    # 이 검사가 없으면 warp_ok가 NULL인 잡이 강등으로 오분류된다.
    if warp_ok not in (_WARP_OK_TRUE, _WARP_OK_FALSE):
        return OBSERVATION_NO_RESULT
    if warp_ok == _WARP_OK_FALSE:
        return OBSERVATION_DEMOTED if has_warped else OBSERVATION_NO_WARP
    # rows가 배열이 아니면 row_count를 신뢰하지 않는다 — rows: null인 잡이
    # 행 미검출로 오분류되는 것을 막는다(curation_service.py:63-64와 같은 방어).
    if rows_type != _ROWS_TYPE_ARRAY or row_count is None:
        return OBSERVATION_NO_RESULT
    return OBSERVATION_UNCONFIRMED if row_count > 0 else OBSERVATION_NO_ROWS

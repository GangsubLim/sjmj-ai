"""금액칸 VLM 판독의 파싱·재시도 정책(stdlib only 순수 로직).

infer_photo는 모듈 레벨 cv2/torch import라 paddle-free 코어 venv에서 import할 수 없다.
파싱·재시도 판단만 경량 모듈로 분리해 가짜 판독기로 단위테스트한다
(infer_job.assemble_result_json과 동일한 경량 순수 모듈 패턴).

이 모듈은 두 경로(`amount_read` / `handwriting.amount_read`)로 각각 로드돼 모듈 객체가
둘이 될 수 있다 — 모듈 전역 가변 상태를 두지 말 것(현재는 순수함수 + 상수뿐).
"""

import re
from collections.abc import Callable

# 재시도 원문 구분자 — 시도별 원문을 이 문자로 join해 재시도 발생·성공 여부를 raw만으로 복원한다.
ATTEMPT_SEP = "→"


def parse_amount(txt: str) -> tuple[int | None, str]:
    """VLM 원문에서 숫자를 연결해 (정수|None, strip된 원문)을 반환한다.

    Args:
        txt: 판독기가 낸 원문 텍스트.

    Returns:
        (숫자를 이어붙인 정수, strip된 원문). 숫자가 하나도 없으면 정수 자리는 None —
        미판독을 0(빈칸)으로 기록하지 않기 위해 0이 아니라 None이다. 반대로 판독값 0은
        정상 판독(빈칸)이므로 None이 아니다.
    """
    digits = "".join(re.findall(r"\d+", txt))
    return (int(digits) if digits else None), txt.strip()


def read_amount_with_retry(
    read_once: Callable[[int], str], attempts: int = 2
) -> tuple[int | None, str]:
    """read_once(attempt) 결과를 파싱하고, 숫자가 0개(퇴화)면 재시도한다.

    Args:
        read_once: attempt 인덱스(0부터 증가)를 받아 판독 원문을 반환하는 콜백.
            호출측은 이 인덱스로 시도마다 다른 임시파일 경로를 만든다. 이 콜백의 예외는
            감싸지 않고 그대로 전파한다(예외는 재시도 트리거가 아니다 — design.md 비목표).
        attempts: 최대 시도 횟수. 기본 2 = 원 호출 1회 + 재시도 1회.

    Returns:
        (정수|None, raw). 퇴화 판정은 `value is not None` 기준이라 판독값 0은 성공으로
        보고 재시도하지 않는다(AMT_PROMPT가 빈칸에 "0"을 지시하므로 0은 흔한 정상값).
        최종 실패 시 정수 자리는 None — 0으로 기록하지 않는다(미판독≠빈칸).
        raw는 시도별 strip 원문을 ATTEMPT_SEP으로 join한 값.
    """
    raws: list[str] = []
    for attempt in range(attempts):
        value, raw = parse_amount(read_once(attempt))
        raws.append(raw)  # 빈 원문도 버리지 않는다 — 시도 횟수가 raw에서 복원돼야 한다
        if value is not None:
            return value, ATTEMPT_SEP.join(raws)
    return None, ATTEMPT_SEP.join(raws)

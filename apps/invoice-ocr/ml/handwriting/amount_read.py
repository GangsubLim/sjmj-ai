"""금액칸 VLM 판독의 파싱·재시도 정책(stdlib only 순수 로직).

infer_photo는 모듈 레벨 cv2/torch import라 paddle-free 코어 venv에서 import할 수 없다.
파싱·재시도 판단만 경량 모듈로 분리해 가짜 판독기로 단위테스트한다
(infer_job.assemble_result_json과 동일한 경량 순수 모듈 패턴).

이 모듈은 두 경로(`amount_read` / `handwriting.amount_read`)로 각각 로드돼 모듈 객체가
둘이 될 수 있다 — 모듈 전역 가변 상태를 두지 말 것(현재는 순수함수 + 상수뿐).
"""

import re


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

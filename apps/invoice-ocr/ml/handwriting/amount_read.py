"""금액칸 VLM 판독의 파싱·재시도 정책(stdlib only 순수 로직).

infer_photo는 모듈 레벨 cv2/torch import라 paddle-free 코어 venv에서 import할 수 없다.
파싱·재시도 판단만 경량 모듈로 분리해 가짜 판독기로 단위테스트한다
(infer_job.assemble_result_json과 동일한 경량 순수 모듈 패턴).

이 모듈은 반드시 `handwriting.amount_read` 패키지 경로로만 로드한다 — flat `import amount_read`는
모듈 객체(따라서 `DegenerateOutputError` 클래스)를 이중화해 worker의 `except`를 빗나가게 한다
(이슈 #99). `tests/test_infer_photo_wiring.py`의 정적 가드가 이를 강제한다. 모듈 전역 가변
상태를 두지 말 것(현재는 순수함수 + 상수뿐) — 이 지침은 여전히 유효하다.
"""

import re
from collections.abc import Callable

# 재시도 원문 구분자 — 시도별 원문을 이 문자로 join해 재시도 발생·성공 여부를 raw에서 읽는다.
# 판독기가 이 문자를 스스로 출력할 가능성은 배제하지 못하므로, 시도 횟수 복원은 보장이 아니라
# 진단용 추정이다(하류는 amount_raw를 표시·전달만 하고 재파싱하지 않는다).
ATTEMPT_SEP = "→"

# degenerate 감지 임계 — 연속 '!'가 이 길이 이상이면 판독기 붕괴로 본다(이슈 #99).
# '!' 한정이 핵심이다. 실측 스팸은 '!'×32(max_tokens)이고, 대시 채움('——————')·빈 원문 같은
# 정상 미판독은 반복 문자여도 걸리면 안 되므로 '반복 문자 일반'으로 일반화하지 않는다.
DEGENERATE_BANG_RUN = 10
# 예외 메시지에 담을 raw 표본 길이 — 워커가 이 메시지를 stderr 로그에 그대로 싣는다.
DEGENERATE_RAW_SAMPLE = 40
_DEGENERATE_RE = re.compile(f"!{{{DEGENERATE_BANG_RUN},}}")


class DegenerateOutputError(RuntimeError):
    """판독기 출력이 degenerate(연속 '!' 스팸)라는 신호.

    추론(handwriting)과 대응(worker) 양쪽이 import하므로 worker가 아니라 이 경량 모듈에 둔다
    (worker→handwriting 단방향 의존 유지).
    """


def is_degenerate_raw(raw: str) -> bool:
    """raw에 연속 '!' DEGENERATE_BANG_RUN자 이상 run이 있으면 True.

    Args:
        raw: 판독기 원문(strip 여부 무관).

    Returns:
        붕괴 출력이면 True. 미판독(빈 원문·대시 채움)은 False — 미판독과 degenerate는
        다른 사건이며 전자는 기존대로 재시도 후 (None, raw)로 남는다.
    """
    return _DEGENERATE_RE.search(raw) is not None


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

    Raises:
        DegenerateOutputError: 어느 시도든 raw가 '!' 스팸이면 재시도 없이 즉시. 데모 CLI
            (infer_photo.process_one)도 같은 read 경로라 이 예외로 죽는다 — 개발 도구에서
            조용한 null보다 가시적 실패가 낫다(의도된 부수효과).
    """
    raws: list[str] = []
    for attempt in range(attempts):
        value, raw = parse_amount(read_once(attempt))
        if is_degenerate_raw(raw):
            # 병합(group.merge_amounts의 '?' 치환) 이전이라 다행 블록 우회가 없다. sticky
            # 붕괴라 재시도는 낭비 호출일 뿐이므로 여기서 멈춘다(spec §1).
            raise DegenerateOutputError(
                f"판독기 출력이 degenerate — 연속 '!' {DEGENERATE_BANG_RUN}자 이상, "
                f"raw 표본: {raw[:DEGENERATE_RAW_SAMPLE]!r}"
            )
        raws.append(raw)  # 빈 원문도 버리지 않는다 — 시도 횟수가 raw에서 복원돼야 한다
        if value is not None:
            return value, ATTEMPT_SEP.join(raws)
    return None, ATTEMPT_SEP.join(raws)


def attempt_png_name(idx: int, attempt: int) -> str:
    """금액칸 idx의 attempt번째 시도가 쓸 고유 임시 PNG 파일명을 만든다.

    Args:
        idx: 전표 내 금액칸 일련번호.
        attempt: 0부터 증가하는 시도 인덱스.

    Returns:
        'amt_{idx}_a{attempt}.png'. 칸끼리도, 같은 칸의 시도끼리도 겹치지 않는다 —
        재시도가 직전 시도의 파일을 덮어쓰지 않게 하는 방어(MLX generate는 경로 입력).
    """
    return f"amt_{idx}_a{attempt}.png"

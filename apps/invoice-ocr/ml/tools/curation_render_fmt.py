"""큐레이션 리포트 렌더 계층이 공유하는 서식 원자 셋.

`pct`·`known_text`는 `curation_render`와 `curation_render_label_source`가 **둘 다** 쓴다.
한쪽에 두고 다른 쪽이 끌어 쓰면 렌더 모듈 사이에 순환이 생긴다(`curation_render` →
`curation_render_label_source` 단방향이 계약이다). `share`는 지금은
`curation_render_label_source`에서만 쓰지만 `pct`의 분모 0·중복 인쇄 짝이라 같은 자리에
둔다 — `pct`가 여기 있는데 짝인 `share`만 다른 모듈에 두면 서식 원자가 흩어진다.

의존 0(다른 tools 모듈을 import하지 않는다) · 전부 순수함수 · stdlib 전용.
"""


def pct(k: int, n: int) -> str:
    """비율을 `k/n (p%)`로 인쇄한다 — 분모 0에서도 **분자는 삼키지 않는다**.

    대부분의 호출부는 `k ≤ n`이 구조적으로 보장돼 `n=0 ⇒ k=0`이지만, 행 수지 절의 둘째 줄은
    분자(training_pairs)와 분모(교정 이력)의 소스가 달라 그 불변식이 깨진다 — 거기서 분자를
    0으로 접으면 하필 이 절이 드러내려는 소스 드리프트 신호가 지워진다.

    잡별 요약 표는 이 함수를 쓰지 않는다 — 그 표는 분모 0에서 분자까지 지우는(`—/0`) 별도
    헬퍼를 쓴다(curation_render 쪽 사설 함수라 이름은 여기서 고정하지 않는다).
    """
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else f"{k}/0 (—)"


def share(k: int, n: int) -> str:
    """비율만 인쇄한다 — 건수는 표의 옆 칸이 이미 낸다. 분모 0이면 '—'.

    `pct`(`k/n (p%)`)를 쓰면 건수 칸과 분자가 중복돼 표가 같은 수를 두 번 말한다.
    """
    return f"{100 * k / n:.1f}%" if n else "—"


def known_text(value: object) -> str:
    """값을 인쇄하되 **모를 때만** '?'로 물러선다 — 렌더는 모르는 것을 말하지 않는다.

    `dict.get(key, "?")`로는 이 폴백이 발화하지 않는다: 생산자(`curation_report._reeval_info`)가
    키를 항상 만들되 None으로 시드하므로 `get`은 기본값이 아니라 저장된 None을 돌려주고, 손상된
    score_meta.json이 리터럴 "None"으로 인쇄돼 진짜 값처럼 읽힌다. truthiness가 아니라
    `is None`으로 판정한다 — n_pairs 0쌍은 유효한 관측치라 '?'로 뭉개면 안 된다.
    """
    return "?" if value is None else str(value)

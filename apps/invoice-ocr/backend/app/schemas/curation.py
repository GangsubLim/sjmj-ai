"""curation 슬라이스 Pydantic 요청 모델. 레포 최초 Pydantic 슬라이스."""

from typing import Annotated, Literal, get_args

from pydantic import BaseModel, StringConstraints, model_validator

# status 화이트리스트 wire 값의 진실원. 타입 별칭이 정본이고 상수는 거기서 파생한다 —
# Literal[...]에 모듈 변수를 넣으면 타입체커가 리터럴로 인정하지 못해 필드가 Any로
# 떨어지기 때문이다. repository의 배제 사유 삭제 조건(update_pair)이 STATUS_EXCLUDED를
# 직접 import해 참조하므로, 값이 여기서만 바뀌어도 양쪽이 함께 반응한다 — 하드코딩
# 사본을 만들지 않는다(리뷰 M1, ADR 0006 §6).
CurationStatus = Literal["included", "excluded"]
STATUS_INCLUDED, STATUS_EXCLUDED = get_args(CurationStatus)

# 학습용 정답 라벨 — 앞뒤 공백은 트림하고, 트림 후 1~200자만 허용(공백뿐인 값 차단).
CanonicalLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CurationPairPatch(BaseModel):
    """학습쌍 부분 갱신 요청 — 잡 세대 토큰 + status/canonical_label 중 하나 이상.

    job_token은 필수다(spec §12). 옵션으로 두면 방어 없는 클라이언트가 살아남아, 재처리
    이전에 열어둔 화면이 옛 그림을 근거로 새 쌍을 덮는 경로가 그대로 남는다.
    """

    job_token: str
    status: CurationStatus | None = None
    canonical_label: CanonicalLabel | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "CurationPairPatch":
        """status와 canonical_label 중 하나 이상을 반드시 지정해야 한다."""
        if self.status is None and self.canonical_label is None:
            raise ValueError("status 또는 canonical_label 중 하나는 필요합니다.")
        return self


class CurationReviewRequest(BaseModel):
    """검수 완료 요청 — 잡 세대 토큰만 받는다(spec §12).

    쌍 수정(PATCH)만 막으면 게이트를 **닫는** 쪽에 구멍이 남는다. 재처리 이전에 열어둔
    화면이 보내는 검수 완료는 새로 생긴 미결 쌍에 reviewed_at을 찍어 사람 눈에 닿기 전에
    큐에서 지우고, 그 상태로 --reembed-job 가드를 통과시킨다(§7 · §11-1).
    """

    job_token: str

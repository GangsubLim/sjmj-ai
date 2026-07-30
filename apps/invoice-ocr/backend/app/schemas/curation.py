"""curation 슬라이스 Pydantic 요청 모델. 레포 최초 Pydantic 슬라이스."""

from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints, model_validator

# status 화이트리스트 wire 값. repository의 배제 사유 삭제 조건(update_pair)이 이
# 상수를 직접 import해 참조한다 — 값이 여기서만 바뀌어도 양쪽이 함께 반응하도록
# 하드코딩 사본을 만들지 않는다(리뷰 M1, ADR 0006 §6).
STATUS_INCLUDED = "included"
STATUS_EXCLUDED = "excluded"

# 학습용 정답 라벨 — 앞뒤 공백은 트림하고, 트림 후 1~200자만 허용(공백뿐인 값 차단).
CanonicalLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class CurationPairPatch(BaseModel):
    """학습쌍 부분 갱신 요청 — status 또는 canonical_label 중 하나 이상."""

    status: Literal[STATUS_INCLUDED, STATUS_EXCLUDED] | None = None
    canonical_label: CanonicalLabel | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "CurationPairPatch":
        """status와 canonical_label 중 하나 이상을 반드시 지정해야 한다."""
        if self.status is None and self.canonical_label is None:
            raise ValueError("status 또는 canonical_label 중 하나는 필요합니다.")
        return self

"""curation 슬라이스 Pydantic 요청 모델. 레포 최초 Pydantic 슬라이스."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CurationPairPatch(BaseModel):
    """학습쌍 부분 갱신 요청 — status 또는 canonical_label 중 하나 이상."""

    status: Literal["included", "excluded"] | None = None
    canonical_label: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _at_least_one(self) -> "CurationPairPatch":
        """status와 canonical_label 중 하나 이상을 반드시 지정해야 한다."""
        if self.status is None and self.canonical_label is None:
            raise ValueError("status 또는 canonical_label 중 하나는 필요합니다.")
        return self

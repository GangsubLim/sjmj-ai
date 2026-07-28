"""ocr 슬라이스 Pydantic 요청 모델. curation에 이은 두 번째 전환 슬라이스."""

from datetime import date
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

# 후보 칩 rank 상한(0-based). ml의 TOPK=5(handwriting/infer_photo.py, tools/bank_update.py)와
# 동기다 — top-K를 바꾸면 여기도 함께 바꾼다.
TOP_K = 5

# label_source 허용 어휘(SSoT). services/ocr_correction.py:37 주석이 이 목록을 참조한다.
# tests/test_label_source_sync.py가 .claude/ai-context/api-spec.json의 label_source enum과
# 이 집합이 동기인지 검증한다 — 값을 바꾸면 spec도 함께 갱신한다.
# Literal이 아니라 TOP_K 파생 frozenset인 이유: candidate_picked:0..{TOP_K-1}이 TOP_K에 종속돼
# 있어 Literal로 나열하면 TOP_K를 바꿀 때마다 리터럴 목록을 손으로 다시 세어야 한다(드리프트
# 위험). frozenset은 TOP_K 변경에 자동 추종한다 — field_validator의 멤버십 검사(in) 비용 차이는
# 무시할 수준이다.
LABEL_SOURCES = frozenset(
    ("top1_kept", "manual_picked", "manual_typed", "new_item_created")
    + tuple(f"candidate_picked:{rank}" for rank in range(TOP_K))
)


def _reject_blank(v: str) -> str:
    """공백만 있는 값을 거부한다(원본은 strip하지 않는다).

    현행 Validator.required(validators.py:16-22)와 동일 의미다. strip_whitespace=True를 쓰면
    저장값과 increment_usage_by_name(invoice_service.py:86)의 매칭 대상이 조용히 바뀐다.

    Args:
        v: 검증할 문자열.

    Returns:
        입력 문자열 원본.

    Raises:
        ValueError: 공백을 제거하면 빈 문자열이 될 때.
    """
    if not v.strip():
        raise ValueError("필수 필드입니다.")
    return v


NonEmptyStr = Annotated[str, AfterValidator(_reject_blank)]
IsoDateStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class OcrConfirmItem(BaseModel):
    """확정 payload의 품목 1건.

    검증 대상은 ocr 슬라이스가 소유하는 두 필드(crop_ref·label_source)뿐이다. 금액·수량 등
    나머지는 invoices 슬라이스의 도메인이며, 이 경로는 전환 전(free-form dict)과 동일하게
    통과시킨다 — 프론트가 quantity/unit_price를 ""·"12" 같은 문자열로도 보내기 때문이다
    (frontend/src/utils/calculations.ts "원래 값 유지"). 여기서 조이면 운영 저장이 깨진다.

    extra="allow"이므로 `label_soruce` 같은 오타 키는 조용히 통과한다(200, provenance만 유실).
    생산자는 프론트 `attachLabelSource` 하나뿐이며, Task 12에서 프론트에 오타 방어선
    (`attachLabelSource` 단일화 + 고정 테스트)을 도입할 예정이다 — 그 전까지는 이 계약이
    수용한 리스크이며, contract 테스트(`test_confirm_typo_label_source_key_is_silently_dropped`)
    가 그 대가(200 + `label_source` null)를 명시적으로 고정한다. extra="forbid"는 채택하지
    않는다(item 필드 10종·top-level 11종을 전부 모델링해야 하고, quantity/unit_price의
    `int | str` 관용을 계약으로 굳히게 된다).
    """

    model_config = ConfigDict(extra="allow")

    crop_ref: str | None = None
    label_source: str | None = None

    @field_validator("label_source")
    @classmethod
    def _known_label_source(cls, v: str | None) -> str | None:
        """허용값 화이트리스트 밖의 label_source를 거부한다.

        Args:
            v: 클라이언트가 보낸 label_source(미전송이면 None).

        Returns:
            검증을 통과한 원본 값.

        Raises:
            ValueError: LABEL_SOURCES에 없는 값일 때.
        """
        if v is not None and v not in LABEL_SOURCES:
            raise ValueError("허용되지 않은 label_source 값입니다.")
        return v


class OcrConfirmRequest(BaseModel):
    """OCR 초안 확정 요청 — 전환 전 Validator(required/date_format/non_empty_array)와 동일 범위."""

    model_config = ConfigDict(extra="allow")

    issue_date: IsoDateStr
    recipient: NonEmptyStr
    items: list[OcrConfirmItem] = Field(min_length=1)

    @field_validator("issue_date")
    @classmethod
    def _real_calendar_date(cls, v: str) -> str:
        """2026-02-30 같은 달력에 없는 날짜를 거부한다(전환 전 strptime 왕복 검증과 동일).

        Args:
            v: YYYY-MM-DD 형식이 확인된 문자열.

        Returns:
            검증을 통과한 원본 값.

        Raises:
            ValueError: 달력에 실재하지 않는 날짜일 때.
        """
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("issue_date는 실재하는 YYYY-MM-DD 날짜여야 합니다.") from None
        return v

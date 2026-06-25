"""숫자 인식 어댑터. stock PaddleOCR(PP-OCRv5) 디코드 후 숫자 post-filter 로
charset 을 사실상 제한(fine-tune 없이 데이터 0). 후속 SP 의 Qwen/TrOCR 는
같은 RecognizerAdapter 인터페이스로 plug-in.
"""
from __future__ import annotations

import re
from typing import Protocol

_NON_DIGIT = re.compile(r"[^0-9]")


def numeric_postfilter(raw: str) -> str:
    """디코드 문자열에서 숫자만 남긴다(천원곱·빈칸 처리는 normalize 담당)."""
    return _NON_DIGIT.sub("", raw)


class RecognizerAdapter(Protocol):
    def recognize(self, crop) -> str:
        ...


class FakeRecognizer:
    """시드 텍스트를 순서대로 반환. 파이프라인 결정론화."""

    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self._i = 0

    def recognize(self, crop) -> str:
        if self._i >= len(self._texts):
            return ""
        out = self._texts[self._i]
        self._i += 1
        return out


class PaddleOCRNumeric:
    """PP-OCRv5 인식 + 숫자 post-filter. 엔진 지연 로딩."""

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import numpy as np  # noqa: PLC0415
            from paddleocr import PaddleOCR  # noqa: PLC0415
            self._np = np
            self._engine = PaddleOCR(show_log=False, lang=self._lang, use_angle_cls=False)
        return self._engine

    def recognize(self, crop) -> str:
        engine = self._ensure_engine()
        arr = self._np.asarray(crop.convert("RGB"))
        result = engine.ocr(arr, det=False, cls=False)
        raw = self._first_text(result)
        return numeric_postfilter(raw)

    @staticmethod
    def _first_text(result) -> str:
        """PaddleOCR rec-only 출력에서 첫 텍스트. 버전별 형태 방어적 처리."""
        if not result:
            return ""
        first = result[0]
        if isinstance(first, (list, tuple)) and first:
            cand = first[0]
            if isinstance(cand, (list, tuple)) and cand:
                return str(cand[0])
            return str(cand)
        return str(first)

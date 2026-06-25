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
    """PaddleOCR 3.x TextRecognition(rec-only) + 숫자 post-filter. 엔진 지연 로딩.

    2.x 의 PaddleOCR(...).ocr(det=False) 는 3.x 에서 제거됐다. 3.x 는 단계별
    컴포넌트(TextRecognition)를 직접 호출한다. crop(PIL.RGB)→BGR ndarray.
    """

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import numpy as np  # noqa: PLC0415
            from paddleocr import TextRecognition  # noqa: PLC0415
            self._np = np
            self._engine = TextRecognition()
        return self._engine

    def _raw_text(self, crop) -> str:
        engine = self._ensure_engine()
        arr = self._np.asarray(crop.convert("RGB"))[:, :, ::-1]  # RGB→BGR
        results = list(engine.predict(arr))
        return self._first_text(results)

    def recognize(self, crop) -> str:
        return numeric_postfilter(self._raw_text(crop))

    @staticmethod
    def _first_text(results) -> str:
        """TextRecognition 출력에서 첫 rec_text. 결과객체 .json 구조 방어적 처리."""
        if not results:
            return ""
        data = getattr(results[0], "json", None)
        if isinstance(data, dict):
            r = data.get("res", data)
            if isinstance(r, dict):
                return str(r.get("rec_text", ""))
        return ""


class PaddleOCRText(PaddleOCRNumeric):
    """post-filter 없이 raw 텍스트 반환(단일 crop 텍스트용)."""

    def recognize(self, crop) -> str:
        return self._raw_text(crop)


class ReferenceOCR:
    """references(인쇄 거래명세서) 전체 OCR → 텍스트 리스트.

    손글씨 원본과 달리 references 는 깨끗한 인쇄체라 PaddleOCR 3.x 파이프라인
    (det+rec)이 잘 읽는다. 결과 텍스트에서 match 가 발행일·금액후보를 뽑는다.
    엔진 지연 로딩."""

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            from paddleocr import PaddleOCR  # noqa: PLC0415
            self._engine = PaddleOCR(
                lang=self._lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return self._engine

    def texts(self, image_path: str) -> list[str]:
        engine = self._ensure_engine()
        results = list(engine.predict(str(image_path)))
        if not results:
            return []
        data = getattr(results[0], "json", None)
        if isinstance(data, dict):
            r = data.get("res", data)
            if isinstance(r, dict):
                return list(r.get("rec_texts", []) or [])
        return []

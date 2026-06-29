"""표 검출 어댑터. 실모델(PP-Structure)은 어댑터 뒤에 숨겨 파이프라인을
검출기 교체에 무관하게 만든다(후속 SP의 YOLO 등 plug-in).

DetectedCell.bbox 는 원본 이미지 좌표계의 (x1,y1,x2,y2).
row_index/col_index 는 표 격자에서의 0-based 위치.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DetectedCell:
    row_index: int
    col_index: int
    bbox: tuple[float, float, float, float]


class DetectorAdapter(Protocol):
    def detect(self, image_path: str) -> list[DetectedCell]: ...


class FakeDetector:
    """이미지 파일명 → 고정 셀 리스트. 단위/스모크 테스트 결정론화."""

    def __init__(self, by_basename: dict[str, list[DetectedCell]]):
        self._by_basename = by_basename

    def detect(self, image_path: str) -> list[DetectedCell]:
        return list(self._by_basename.get(os.path.basename(image_path), []))


class TextDetCellDetector:
    """PaddleOCR 3.x TextDetection 박스 → 행/열 비닝 → DetectedCell.

    인쇄표 모델(PP-Structure)이 손글씨 양식을 표로 못 잡고(스파이크 확인),
    괘선도 사진 편차(기울기·연한 인쇄선)에 취약하므로, 동작이 확인된
    텍스트 검출 박스를 셀 위치화에 쓴다. 사진 기울기는 폴리곤 상변 각도로
    추정해 회전좌표에서 군집하고(행·열 인덱스), crop 용 bbox 는 원좌표를 둔다.
    엔진은 지연 로딩.
    """

    def __init__(self):
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            import math  # noqa: PLC0415

            import numpy as np  # noqa: PLC0415
            from paddleocr import TextDetection  # noqa: PLC0415

            self._math = math
            self._np = np
            self._engine = TextDetection()
        return self._engine

    def detect(self, image_path: str) -> list[DetectedCell]:
        engine = self._ensure_engine()
        results = list(engine.predict(image_path))
        polys = _result_polys(results)
        if not polys:
            return []
        rects = [_poly_rect(p) for p in polys]
        angle = _median_skew(polys, self._math)
        # 회전좌표(skew 제거)에서 행/열 군집 — crop bbox 는 원좌표 유지.
        rcx, rcy = [], []
        cos = self._math.cos(-angle)
        sin = self._math.sin(-angle)
        for x1, y1, x2, y2 in rects:
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            rcx.append(cx * cos - cy * sin)
            rcy.append(cx * sin + cy * cos)
        heights = sorted(r[3] - r[1] for r in rects)
        med_h = heights[len(heights) // 2] if heights else 10.0
        row_idx = _cluster_index(rcy, gap=med_h * 0.6)
        col_idx = _cluster_index(rcx, gap=med_h * 0.9)
        cells = [
            DetectedCell(row_index=ri, col_index=ci, bbox=rect)
            for rect, ri, ci in zip(rects, row_idx, col_idx)
        ]
        return cells


def _result_polys(results) -> list:
    """TextDetection 결과객체 → dt_polys 리스트(방어적)."""
    if not results:
        return []
    data = getattr(results[0], "json", None)
    if isinstance(data, dict):
        r = data.get("res", data)
        if isinstance(r, dict):
            return list(r.get("dt_polys", []) or [])
    return []


def _poly_rect(poly) -> tuple[float, float, float, float]:
    xs = [float(pt[0]) for pt in poly]
    ys = [float(pt[1]) for pt in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _median_skew(polys, math) -> float:
    """각 폴리곤 상변(p0→p1) 각도의 중앙값(rad). 수평에 가까운 것만."""
    angs = []
    for poly in polys:
        (x0, y0), (x1, y1) = poly[0], poly[1]
        a = math.atan2(float(y1) - float(y0), float(x1) - float(x0))
        if abs(a) < math.radians(25):
            angs.append(a)
    if not angs:
        return 0.0
    angs.sort()
    return angs[len(angs) // 2]


def _cluster_index(centers: list[float], gap: float) -> list[int]:
    """1D 중심값들을 정렬·간격(gap) 기준으로 군집해 0-based 인덱스 부여."""
    if not centers:
        return []
    order = sorted(range(len(centers)), key=lambda i: centers[i])
    threshold = max(gap, 1.0)
    idx = [0] * len(centers)
    cur = 0
    for pos, oi in enumerate(order):
        if pos > 0 and centers[oi] - centers[order[pos - 1]] > threshold:
            cur += 1
        idx[oi] = cur
    return idx

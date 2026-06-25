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
    def detect(self, image_path: str) -> list[DetectedCell]:
        ...


class FakeDetector:
    """이미지 파일명 → 고정 셀 리스트. 단위/스모크 테스트 결정론화."""

    def __init__(self, by_basename: dict[str, list[DetectedCell]]):
        self._by_basename = by_basename

    def detect(self, image_path: str) -> list[DetectedCell]:
        return list(self._by_basename.get(os.path.basename(image_path), []))


class PPStructureDetector:
    """PaddleOCR PP-Structure 표인식 → DetectedCell 리스트.

    실제 출력 매핑은 Task 1 스파이크로 확정한 구조에 맞춘다. 엔진 로딩은
    지연(첫 detect)해 import·테스트 비용을 낮춘다.
    """

    def __init__(self, lang: str = "korean"):
        self._lang = lang
        self._engine = None

    def _ensure_engine(self):
        if self._engine is None:
            from paddleocr import PPStructure  # noqa: PLC0415
            self._engine = PPStructure(show_log=False, lang=self._lang)
        return self._engine

    def detect(self, image_path: str) -> list[DetectedCell]:
        engine = self._ensure_engine()
        result = engine(image_path)
        cells: list[DetectedCell] = []
        for block in result:
            if block.get("type") != "table":
                continue
            res = block.get("res") or {}
            for grid_cell in self._iter_grid_cells(res):
                cells.append(grid_cell)
        return cells

    @staticmethod
    def _iter_grid_cells(res: dict):
        """PP-Structure table res → DetectedCell. 셀 박스를 y→행, x→열로 격자화.

        스파이크에서 본 cell_bbox 형식(4-좌표 또는 8-좌표)을 (x1,y1,x2,y2)로
        환산한 뒤, y 중심으로 행을, x 중심으로 열을 군집해 인덱스를 매긴다.
        """
        boxes = res.get("cell_bbox") or []
        rects: list[tuple[float, float, float, float]] = []
        for b in boxes:
            flat = list(b)
            if len(flat) == 4:
                rects.append((flat[0], flat[1], flat[2], flat[3]))
            else:
                xs = flat[0::2]
                ys = flat[1::2]
                rects.append((min(xs), min(ys), max(xs), max(ys)))
        row_idx = _cluster_index([(r[1] + r[3]) / 2 for r in rects])
        col_idx = _cluster_index([(r[0] + r[2]) / 2 for r in rects])
        for rect, ri, ci in zip(rects, row_idx, col_idx):
            yield DetectedCell(row_index=ri, col_index=ci, bbox=rect)


def _cluster_index(centers: list[float], gap_ratio: float = 0.5) -> list[int]:
    """1D 중심값들을 정렬·간격 기준으로 군집해 0-based 인덱스 부여."""
    if not centers:
        return []
    order = sorted(range(len(centers)), key=lambda i: centers[i])
    spans = [centers[order[i + 1]] - centers[order[i]] for i in range(len(order) - 1)]
    typical = sorted(spans)[len(spans) // 2] if spans else 0.0
    threshold = max(typical * gap_ratio, 1.0)
    idx = [0] * len(centers)
    cur = 0
    for pos, oi in enumerate(order):
        if pos > 0 and centers[oi] - centers[order[pos - 1]] > threshold:
            cur += 1
        idx[oi] = cur
    return idx

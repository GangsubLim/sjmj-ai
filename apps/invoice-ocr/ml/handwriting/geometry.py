"""단계 기하 사이드카(crop_dir/geometry.json)의 조립·기록 — 순수 계층(stdlib + handwriting.group).

계약 정본은 이 모듈이다(ADR 0012). 소비자는 백엔드(generation 한 키만 읽는다)와 프론트
(version으로 렌더 여부를 가른다)이며, 백엔드는 스키마를 검증하지 않는다.

⚠️ numpy·cv2를 import하지 않는다. 상류(infer_job)가 numpy 스칼라를 넘기므로 경계에서
   int()/float()로 좁혀 json이 직렬화할 수 있게 만드는 것이 이 모듈의 책임이다 —
   좁히지 않으면 json.dump가 TypeError로 죽고, 기록 실패를 삼키는 계약 때문에 그 사실이
   조용히 사라진다.
"""

import contextlib
import json
import os
import sys
from pathlib import Path

# 크롭 대상 판정 술어를 group.py의 정의와 같은 상수로 잇는다 — 술어 동치를 주석 규율이
# 아니라 **같은 상수**로 보장한다. group.py는 모듈 의존이 dataclasses뿐인 순수 코어라 이
# import가 paddle-free 제약을 깨지 않는다.
from handwriting.group import ROW_NEW

# 계약 버전. 키 의미가 바뀌면 올린다 — 프론트는 모르는 version이면 패널을 렌더하지 않고
# 안내 문구로 닫는다(spec §5-1). 값을 올릴 때 프론트의 상수도 함께 올릴 것 — 이 동기는
# tests/test_geometry_version_sync.py가 CI에서 강제한다.
GEOMETRY_VERSION = 1
GEOMETRY_FILENAME = "geometry.json"


def _pair(values) -> list[int]:
    """두 값을 정수 쌍으로 좁힌다(numpy 스칼라 포함)."""
    a, b = values
    return [int(a), int(b)]


def row_geometry(rows) -> list[dict]:
    """prop.rows 전량을 geometry rows[]로 직렬화한다 — row_index는 크롭된 new 행에만.

    rows 배열은 prop.rows와 **평행하다**(cont·empty·total도 전부 들어간다). 행 분류가
    틀어졌는지 보려면 강등된 행 자체가 화면에 있어야 하기 때문이다.

    Args:
        rows: group.build_proposal이 낸 Row 시퀀스(밴드 순서).

    Returns:
        {"band", "type", "item_box", "row_index"} dict 리스트. 크롭되지 않는 행은
        item_box·row_index가 모두 None이다.
    """
    out = []
    index = 0
    for r in rows:
        cropped = r.rtype == ROW_NEW and bool(r.box)
        out.append(
            {
                "band": _pair(r.band),
                "type": str(r.rtype),
                "item_box": _pair(r.box) if cropped else None,
                "row_index": index if cropped else None,
            }
        )
        if cropped:
            index += 1
    return out


def build_geometry(
    *,
    generation: int,
    image_size,
    warp_size,
    quad=None,
    quad_source=None,
    deskew_deg=None,
    hlines=None,
    pitch=None,
    item_x=None,
    amount_x=None,
    rows=None,
) -> dict:
    """단계 기하 문서를 만든다 — 도달하지 못한 단계의 키는 넣지 않는다(부분 문서가 정상).

    부재와 null을 가른다: 강등 잡에는 hlines·rows 키가 **아예 없고**, 키가 있는데 비어
    있으면 "검출했는데 0건"이라는 다른 사실이다.

    Args:
        generation: 잡 점유 시점의 ocr_jobs.reprocess_seq(migration_014).
        image_size: EXIF 적용 후 원본 (width, height). 프론트 viewBox가 이 값에 의존한다.
        warp_size: 워프 결과 (width, height). 상수를 하드코딩하지 않고 파일이 진실이다.
        quad: 4개의 (x, y) 쌍을 내는 시퀀스(ndarray 허용) 또는 None.
        quad_source: corner_dl.quad_candidates가 yield한 "dl" | "color" 또는 None.
        deskew_deg: 적용한 deskew 각도(도).
        hlines: 검출한 가로줄 y 시퀀스.
        pitch: 행 피치.
        item_x: 품목 크롭의 실제 (x0, x1).
        amount_x: 금액 크롭의 실제 (x0, x1) — 좌측은 그 잡의 실측이다(#50).
        rows: row_geometry() 산출.

    Returns:
        json 직렬화 가능한 dict.
    """
    doc: dict = {
        "version": GEOMETRY_VERSION,
        "generation": int(generation),
        "image_size": _pair(image_size),
        "warp_size": _pair(warp_size),
        "quad": None if quad is None else [[float(p[0]), float(p[1])] for p in quad],
        "quad_source": None if quad_source is None else str(quad_source),
        "deskew_deg": None if deskew_deg is None else float(deskew_deg),
    }
    if hlines is not None:
        doc["hlines"] = [int(y) for y in hlines]
    if pitch is not None:
        doc["pitch"] = float(pitch)
    if item_x is not None:
        doc["item_x"] = _pair(item_x)
    if amount_x is not None:
        doc["amount_x"] = _pair(amount_x)
    if rows is not None:
        doc["rows"] = rows
    return doc


def write_geometry(crop_dir, doc: dict) -> bool:
    """crop_dir/geometry.json에 원자적으로 남긴다 — 실패는 삼키고 False를 돌려준다.

    **삼키는 것이 계약이다.** 기하는 진단이지 산출물이 아니라 기록 실패가 추론을 죽여서는
    안 된다(spec §5-2). 그래서 **원자성이 필수다** — 삼키는 이상 잘린 JSON이 남을 수 있고,
    그 tmp_dir이 정상 커밋 후 worker.poll._swap_crop_dir로 교체되면 잘린 파일이 그대로
    노출된다. 임시 파일에 덤프한 뒤 os.replace로 갈아끼우므로, 실패 시 남는 것은 직전
    상태(부재 또는 이전 완전 문서)다. 보장 범위는 **프로세스 크래시**까지다 — os.replace는
    rename의 원자성만 보장하고 전원 손실 구간의 내구성은 fsync가 필요하다. 진단 사이드카에
    잡마다 동기 I/O를 물리지 않는다.

    Args:
        crop_dir: 이 잡의 crop 산출물 디렉터리.
        doc: build_geometry가 만든 문서.

    Returns:
        기록 성공 여부.
    """
    target = Path(crop_dir) / GEOMETRY_FILENAME
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, target)
        return True
    except (OSError, TypeError, ValueError) as exc:
        # 광범위하되 닫혀 있다 — 디스크/권한(OSError), 직렬화 불가(TypeError),
        # 순환 참조·NaN 등(ValueError). 창구는 stderr(ml-worker.err.log)로 모은다.
        print(
            f"[geometry] 기록 실패({target}): {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        # ValueError까지 넓히는 이유는 NUL 경로 등에서 Path.unlink가 OSError가 아닌
        # ValueError를 내며(실측: ValueError: unlink: embedded null character in path),
        # 정리 실패가 삼킴 계약을 뚫으면 안 되기 때문. contextlib.suppress는 SIM105
        # (ruff select에 SIM 포함, handwriting/** per-file-ignores에 SIM 면제 없음) 회피도 겸함.
        with contextlib.suppress(OSError, ValueError):
            tmp.unlink(missing_ok=True)
        return False

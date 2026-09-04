import re
from pathlib import Path


def test_geometry_version_matches_the_frontend_constant():
    """ml은 TS를 import할 수 없다 — 두 상수를 소스 텍스트 정규식으로 읽어 대조한다.

    드리프트 시 화면이 전량 "모르는 기하 형식"으로 닫히므로, 이 동기는 CI가 강제해야 한다.
    """
    ml_src = (Path(__file__).resolve().parents[1] / "handwriting" / "geometry.py").read_text(
        encoding="utf-8"
    )
    ts_src = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "types" / "curation.ts"
    ).read_text(encoding="utf-8")

    ml_match = re.search(r"^GEOMETRY_VERSION = (\d+)$", ml_src, re.MULTILINE)
    ts_match = re.search(r"STAGE_GEOMETRY_VERSION = (\d+)", ts_src)

    assert ml_match and ts_match, "버전 상수를 소스에서 찾지 못했다"
    assert ml_match.group(1) == ts_match.group(1)

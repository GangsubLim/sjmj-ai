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


def test_geometry_filename_matches_the_backend_literal():
    """백엔드는 ml을 import할 수 없어 파일명을 리터럴로 든다 — 두 리터럴을 소스 텍스트 정규식으로 대조한다.

    드리프트 시 전 잡이 404("단계 기하가 없습니다")로 조용히 닫혀 진단 기능 자체가 관측
    없음으로 위장한다 — version 드리프트가 화면 경고로 보이는 것과 달리 실패 모드가 더
    나쁘다. version 축과 같은 소스 정규식 관용구로 filename 축도 CI가 강제해야 한다.
    """
    ml_src = (Path(__file__).resolve().parents[1] / "handwriting" / "geometry.py").read_text(
        encoding="utf-8"
    )
    backend_src = (
        Path(__file__).resolve().parents[2] / "backend" / "app" / "services" / "curation_service.py"
    ).read_text(encoding="utf-8")

    ml_match = re.search(r'^GEOMETRY_FILENAME = "([^"]+)"$', ml_src, re.MULTILINE)
    backend_match = re.search(r'crop_dir\(job_id\) / "([^"]+\.json)"', backend_src)

    assert ml_match and backend_match, "파일명 상수/리터럴을 소스에서 찾지 못했다"
    assert ml_match.group(1) == backend_match.group(1)

"""배포 워크플로우 불변식 — 프론트 빌드가 옛 콘텐츠 해시 청크를 지우지 않는다."""

import re
from pathlib import Path

# 백엔드 tests 기준 레포 루트: tests → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "deploy.yml"
_BUILD_LINE = re.compile(r"npm run build\b")
_FLAG_WITH_SEPARATOR = "-- --no-emptyOutDir"


def test_frontend_build_preserves_old_chunks() -> None:
    """정방향·롤백 두 프론트 빌드 모두 `-- --no-emptyOutDir`(구분자 포함)로 실행돼야 한다.

    플래그가 빠지면 배포가 dist를 비워, 배포 전에 열려 있던 탭이 아직 요청하지 않은
    콘텐츠 해시 청크(page-*.js, jspdf)가 404가 된다. 특히 롤백 경로 누락은 실제
    롤백 시점(프로덕션)에만 드러나므로 여기서 고정한다. `--` 구분자 없이 플래그만
    붙이면(예: `npm run build --no-emptyOutDir`) npm이 이를 스크립트에 전달하지 않고
    조용히 삭제해 dist가 다시 비워지는데도 CI는 녹색이 되므로, 구분자 포함 여부까지 검사한다.
    """
    assert _WORKFLOW.is_file(), f"missing workflow at {_WORKFLOW}"
    # encoding 명시: 한글 주석이 든 파일을 읽는다(로케일 기본 인코딩에 맡기면
    # LANG=C 환경(launchd·self-hosted 러너)에서 드리프트와 무관한 UnicodeDecodeError로 깨진다).
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    builds = [
        (num, line.strip()) for num, line in enumerate(lines, start=1) if _BUILD_LINE.search(line)
    ]
    assert len(builds) == 2, f"expected 2 npm run build lines (forward+rollback), got {builds}"
    missing = [item for item in builds if _FLAG_WITH_SEPARATOR not in item[1]]
    assert not missing, "; ".join(f"deploy.yml:{num}: {line}" for num, line in missing)

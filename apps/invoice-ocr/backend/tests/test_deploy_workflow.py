"""배포 워크플로우 불변식 — 프론트 빌드가 옛 콘텐츠 해시 청크를 지우지 않는다."""

import re
from pathlib import Path

# 백엔드 tests 기준 레포 루트: tests → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "deploy.yml"
# `build:analyze` 같은 다른 스크립트를 빌드 라인으로 오인하지 않도록 뒤 토큰을 막는다.
_BUILD_LINE = re.compile(r"npm run build(?![\w:.-])")
# `--` 구분자 뒤 공백 개수는 셸/npm에 무의미하므로 \s+로 허용한다(표기 차이로 인한 거짓 실패 방지).
_BUILD_WITH_FLAG = re.compile(r"npm run build\s+--\s+--no-emptyOutDir\b")


def _command(line: str) -> str:
    """YAML 주석(`#` 이후)을 잘라낸 실행 명령 부분만 남긴다.

    원시 라인으로 검사하면 주석이 명령으로 오인된다 — 설명에 `npm run build`가
    한 번 등장하면 빌드가 3개로 세어져 거짓 실패하고, 반대로
    `npm run build  # 예전엔 -- --no-emptyOutDir 였다`처럼 플래그를 주석으로 밀면
    검사가 통과하는데도 실제 배포는 dist를 비운다(막으려던 회귀가 녹색으로 통과).
    """
    return line.split("#", 1)[0].strip()


def test_frontend_build_preserves_old_chunks() -> None:
    """정방향·롤백 두 프론트 빌드 모두 `-- --no-emptyOutDir`(구분자 포함)로 실행돼야 한다.

    플래그가 빠지면 배포가 dist를 비워, 배포 전에 열려 있던 탭이 아직 요청하지 않은
    콘텐츠 해시 청크(page-*.js, jspdf)가 404가 된다. 특히 롤백 경로 누락은 실제
    롤백 시점(프로덕션)에만 드러나므로 여기서 고정한다. `--` 구분자 없이 플래그만
    붙이면(예: `npm run build --no-emptyOutDir`) npm이 이를 스크립트에 전달하지 않고
    조용히 삭제해 dist가 다시 비워지는데도 CI는 녹색이 되므로, 구분자 포함 여부까지 검사한다.
    검사 대상은 주석을 제거한 명령부(`_command`)다.
    """
    assert _WORKFLOW.is_file(), f"missing workflow at {_WORKFLOW}"
    # encoding 명시: 한글 주석이 든 파일을 읽는다(로케일 기본 인코딩에 맡기면
    # LANG=C 환경(launchd·self-hosted 러너)에서 드리프트와 무관한 UnicodeDecodeError로 깨진다).
    lines = _WORKFLOW.read_text(encoding="utf-8").splitlines()
    commands = ((num, _command(line)) for num, line in enumerate(lines, start=1))
    builds = [(num, cmd) for num, cmd in commands if _BUILD_LINE.search(cmd)]
    assert len(builds) == 2, f"expected 2 npm run build lines (forward+rollback), got {builds}"
    missing = [item for item in builds if not _BUILD_WITH_FLAG.search(item[1])]
    assert not missing, "; ".join(f"deploy.yml:{num}: {line}" for num, line in missing)

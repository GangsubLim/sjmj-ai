"""배포 워크플로우 불변식 — 프론트 빌드 청크 보존·공급망 게이트 순서·롤백 범위 분리·pending 생애주기."""

import re
from pathlib import Path

import yaml

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


def test_rollback_uses_env_for_previous_sha() -> None:
    """롤백 step이 PREV를 run 블록 보간이 아니라 env로 받아야 한다(template-injection 회귀 방지).

    PREV가 비어 있으면 즉시 실패해야 한다 — 빈 값으로 `git checkout --force`가
    실행되면 워킹 디렉터리가 예측 불가능한 상태가 된다.
    """
    text = _WORKFLOW.read_text(encoding="utf-8")
    rollback = text.split("- name: Rollback on failure", 1)[1]
    rollback = rollback.split("\n      - name:", 1)[
        0
    ]  # 다음 step 앞에서 자른다 — 이후에 붙는 무관한 step이 run_block으로 새어 들어가는 것을 막는다
    env_block, run_block = rollback.split("run:", 1)
    assert "PREV: ${{ steps.previous.outputs.sha }}" in env_block
    assert "${{ steps.previous.outputs.sha }}" not in run_block
    assert '[[ -n "$PREV" ]]' in run_block


def _deploy_steps() -> tuple[list[dict], dict[str, int]]:
    """deploy 잡의 step 목록과 이름→인덱스 맵을 돌려준다."""
    job = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]
    steps = job["steps"]
    return steps, {step.get("name", ""): i for i, step in enumerate(steps)}


def test_supply_chain_gate_precedes_db_and_install_steps() -> None:
    """게이트는 체크아웃 직후·DB 백업과 설치 이전에 있어야 한다.

    uv sync의 sdist setup.py와 npm ci의 lifecycle script는 실행 그 자체가 침해 시점이라
    설치 이후 차단은 방어가 아니다. 게이트가 막히면 운영 DB 백업·마이그레이션에도
    진입하지 않는다.
    """
    steps, order = _deploy_steps()
    gate = "Supply-chain freshness gate (pre-install)"
    assert order["Checkout target"] < order[gate]
    assert order[gate] < order["Mark install phase entered"]
    assert order["Mark install phase entered"] < order["Backup operational DB"]
    assert order["Mark install phase entered"] < order["Backend deps + import smoke"]
    assert steps[order[gate]]["if"] == (
        "${{ github.event_name != 'workflow_dispatch' || inputs.skip_supply_chain_gate != true }}"
    )


def test_rollback_scope_is_split_by_install_phase_marker() -> None:
    """게이트·체크아웃 실패는 작업트리만 복원하고 실행 중인 서비스를 건드리지 않아야 한다.

    취소도 함께 잡는다 — `failure()`는 취소 상태를 포함하지 않아, 게이트 도중 취소되면
    운영 체크아웃에 미검증 target SHA가 남고 다음 배포가 그 SHA를 base로 삼는다.
    """
    steps, order = _deploy_steps()
    restore = steps[order["Restore checkout on pre-install failure"]]
    assert restore["if"] == (
        "${{ (failure() || cancelled()) && steps.phase.outputs.entered != 'true' }}"
    )
    assert "npm ci" not in restore["run"]
    assert "install-launchagent" not in restore["run"]
    rollback = steps[order["Rollback on failure"]]
    assert rollback["if"] == "${{ failure() && steps.phase.outputs.entered == 'true' }}"


def test_pending_gate_base_is_cleared_only_after_deploy_success() -> None:
    """우회 구간의 소급 검사 의무는 배포가 실제로 성공한 뒤에만 해제돼야 한다.

    게이트 통과 직후 지우면, 뒤 단계 실패로 이전 SHA로 롤백됐을 때 검사되지 않은
    우회 구간이 운영에 남은 채 기록만 사라진다.
    """
    steps, order = _deploy_steps()
    gate = steps[order["Supply-chain freshness gate (pre-install)"]]
    assert gate["id"] == "gate"
    assert "rm -f" not in gate["run"]
    clear = "Clear pending gate base"
    assert order[clear] > order["ml-worker liveness"]
    assert steps[order[clear]]["if"] == "${{ success() && steps.gate.outputs.checked == 'true' }}"
    assert "rm -f" in steps[order[clear]]["run"]

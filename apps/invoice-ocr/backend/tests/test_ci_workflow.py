"""CI 워크플로 불변식 — freshness 게이트가 설치 잡보다 앞서고 skip이 통과로 세지 않는다."""

from pathlib import Path

# pyyaml은 uvicorn[standard](직접 의존)로 항상 설치된다. 부재 시 skip이 아니라 수집 에러로
# 드러나야 한다 — 이 파일이 조용히 빠지면 고정하려는 우회로가 다시 열린다.
import yaml

# 백엔드 tests 기준 레포 루트: tests → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_INSTALL_MARKERS = ("npm ci", "uv sync")


def _jobs() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _strip_comments(run: str) -> str:
    """`run:` 블록의 각 줄에서 `#` 주석을 잘라내고 이어붙인 명령 텍스트를 돌려준다.

    원시 텍스트로 매칭하면 설명 주석에 마커 문자열이 등장해 거짓 실패하거나, 반대로
    실제 마커를 주석으로 가려 검사를 피해가는 거짓 통과가 될 수 있다.

    Args:
        run: step의 `run:` 원문.

    Returns:
        주석을 제거한 명령 텍스트.
    """
    return "\n".join(line.split("#", 1)[0] for line in run.splitlines())


def test_install_jobs_are_chained_to_freshness_gate() -> None:
    """frontend·backend·ml은 freshness에 체인되고 첫 step에서 그 결과를 명시 검증해야 한다.

    freshness 실패로 잡이 skip되면 GitHub는 skipped required check를 success로 인정해
    머지 버튼이 열린다. `needs` + `if: !cancelled()` + 첫 step 검증 셋 중 하나라도 빠지면
    그 우회로가 되살아나므로 세 축을 함께 고정한다.
    """
    jobs = _jobs()
    for name in ("frontend", "backend", "ml"):
        job = jobs[name]
        assert job["needs"] == ["freshness"], name
        assert job["if"] == "${{ !cancelled() }}", name
        first = job["steps"][0]
        assert first["name"] == "Verify prerequisite gates", name
        assert first["working-directory"] == "${{ github.workspace }}", name
        assert first["env"]["FRESHNESS_RESULT"] == "${{ needs.freshness.result }}", name
        assert '"$FRESHNESS_RESULT" = "success"' in first["run"], name
        installs = [
            i
            for i, step in enumerate(job["steps"])
            if any(marker in _strip_comments(step.get("run", "")) for marker in _INSTALL_MARKERS)
        ]
        assert installs and min(installs) > 0, name


def test_freshness_job_installs_nothing() -> None:
    """게이트 잡이 의존성을 설치하면 게이트 자체가 무의미해진다.

    npm lifecycle script와 sdist setup.py는 실행 그 자체가 침해 시점이라 설치 이후의
    차단은 방어가 아니다.
    """
    steps = _jobs()["freshness"]["steps"]
    offenders = [
        step.get("name", step.get("uses", "?"))
        for step in steps
        if any(marker in _strip_comments(step.get("run", "")) for marker in _INSTALL_MARKERS)
    ]
    assert not offenders, offenders


def test_release_promotion_exemption_is_shell_not_step_if() -> None:
    """릴리스 승격 면제는 step `if`가 아니라 셸의 대소문자 구분 비교로만 판정돼야 한다.

    GitHub Actions 표현식의 `==`·`startsWith`는 대소문자를 무시해 `DEVEL`·`Release/x`
    같은 브랜치까지 면제에 들어오므로, 면제를 step `if`로 옮기면 이 회귀가 조용히
    되살아난다. hotfix/*는 면제 대상이 아니므로 셸 조건에 등장하면 면제 범위가
    부주의하게 넓어진 것이다.

    원시 텍스트를 그대로 매칭하면 유지보수자가 실제 셸 조건에서 절을 지우고 같은
    줄의 인라인 `#` 주석에만 남겨도 이 검증이 계속 통과해 보호가 사라진 채 거짓
    GREEN이 된다. 그중 `"$HEAD_REPO" == "$THIS_REPO"` 절은 fork 브랜치명 위조를
    막는 가장 무거운 조건이라 손실이 가장 크다.
    """
    gate = next(
        step
        for step in _jobs()["freshness"]["steps"]
        if step.get("name") == "Lockfile freshness gate (7d, pre-install)"
    )
    assert "if" not in gate
    run = _strip_comments(gate["run"])
    assert '"$BASE_REF" == "main"' in run
    assert '"$HEAD_REPO" == "$THIS_REPO"' in run
    assert '"$HEAD_REF" == release/*' in run
    assert "hotfix/" not in run

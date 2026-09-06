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
            if any(marker in step.get("run", "") for marker in _INSTALL_MARKERS)
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
        if any(marker in step.get("run", "") for marker in _INSTALL_MARKERS)
    ]
    assert not offenders, offenders

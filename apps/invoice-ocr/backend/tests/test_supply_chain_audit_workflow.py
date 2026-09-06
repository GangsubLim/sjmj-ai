"""주기 공급망 audit 워크플로 불변식 — 커버리지 하한과 이슈 upsert 키를 고정한다."""

from pathlib import Path

# pyyaml은 uvicorn[standard](직접 의존)로 항상 설치된다. 부재 시 skip이 아니라 수집 에러로
# 드러나야 한다 — 이 파일이 조용히 빠지면 고정하려는 계약이 다시 흔들린다.
import yaml

# 백엔드 tests 기준 레포 루트: tests → backend → invoice-ocr → apps → repo
_REPO_ROOT = Path(__file__).resolve().parents[4]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "supply-chain-audit.yml"


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _scan_step() -> dict:
    """OSV-Scanner를 실행하는 step(id: scan)을 돌려준다."""
    steps = _workflow()["jobs"]["osv-baseline"]["steps"]
    return next(step for step in steps if step.get("id") == "scan")


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


def _step_run(name: str) -> str:
    """osv-baseline 잡에서 이름으로 step을 찾아 주석을 제거한 `run:` 텍스트를 돌려준다.

    워크플로 전문을 건초더미로 쓰면 마커가 무관한 step으로 옮겨가도 통과하므로 로직을
    실제로 소유한 step으로 범위를 좁힌다.

    Args:
        name: step의 `name:` 값.

    Returns:
        해당 step의 주석 제거된 명령 텍스트.
    """
    steps = _workflow()["jobs"]["osv-baseline"]["steps"]
    step = next(s for s in steps if s.get("name") == name)
    return _strip_comments(step["run"])


def test_explicit_lockfile_args_cover_all_three_axes_and_exist() -> None:
    """명시 --lockfile 3축이 실경로와 어긋나면 커버리지 구멍이 조용히 생긴다.

    `--recursive ./`만으로는 하한이 없다 — lockfile이 옮겨지거나 이름이 바뀌면 스캐너는
    에러 없이 그 파일을 못 찾을 뿐이고, 나머지 축이 clean이면 0건으로 집계돼 기존 이슈가
    자동 close된다. 명시 인자는 부재를 hard error로 바꾸는 장치이므로 그 경로가 실제로
    존재하는지를 여기서 고정한다.
    """
    args = _scan_step()["with"]["scan-args"].split()
    assert "--recursive" in args
    lockfiles = [a.split("=", 1)[1] for a in args if a.startswith("--lockfile=")]
    assert sorted(lockfiles) == [
        "apps/invoice-ocr/backend/uv.lock",
        "apps/invoice-ocr/frontend/package-lock.json",
        "apps/invoice-ocr/ml/uv.lock",
    ]
    missing = [path for path in lockfiles if not (_REPO_ROOT / path).is_file()]
    assert not missing, missing


def test_issue_upsert_keys_stay_stable() -> None:
    """제목과 라벨은 upsert의 키다 — 바뀌면 매일 새 이슈가 쌓인다.

    라벨은 docs/agents/triage-labels.md 규약의 needs-triage를 쓴다(이 레포에 security
    라벨은 없다). 무변경 edit 생략은 본문 digest 마커에 의존하므로 함께 고정한다.
    검사는 각 로직을 소유한 step의 주석 제거된 `run:`으로 범위를 좁힌다 — 워크플로 전문을
    원시 매칭하면 조건을 주석으로 강등해도 통과한다.
    """
    upsert = _step_run("Upsert baseline issue")
    summarize = _step_run("Summarize scan result")
    assert 'TITLE="[audit] baseline vulnerabilities (${AUDIT_REF})"' in upsert
    assert "--label needs-triage" in upsert
    # digest 마커는 summarize가 만들고 upsert가 읽는다 — 한쪽만 드리프트해도 잡히도록 양끝을 고정
    assert "audit-digest: " in summarize
    assert "audit-digest: " in upsert


def test_schedule_and_permissions_stay_pinned() -> None:
    """트리거와 권한이 빠지면 트랙 자체가 조용히 사라진다.

    pyyaml은 YAML 1.1 규칙으로 `on:` 키를 boolean True로 파싱하므로 그 키로 접근한다.
    permissions는 이슈 upsert의 전제이자 상한이다 — issues: write가 빠지면 매 실행이
    이슈 단계에서만 죽고, contents: read를 넘기면 관측 트랙이 쓰기 권한을 갖는다.
    """
    workflow = _workflow()
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "0 0 * * *"}]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}


def test_guards_against_orphan_issues_and_silent_degrade() -> None:
    """비-default ref 가드와 degrade 판정은 이슈 트랙의 오독 방지선이다.

    가드가 빠지면 승격 전 workflow_dispatch 리허설이 orphan 이슈를 남기고, degrade 판정이
    빠지면 스캐너가 부분 실패한 0건을 clean으로 오독해 기존 이슈를 자동 close한다.
    검사는 각 판정을 소유한 step의 주석 제거된 `run:`으로 범위를 좁힌다 — 워크플로 전문을
    원시 매칭하면 조건을 주석으로 강등해도 통과한다.
    """
    assert 'if [ "$AUDIT_REF" != "$DEFAULT_BRANCH" ]; then' in _step_run("Upsert baseline issue")
    assert 'if outcome != "success" and not rows:' in _step_run("Summarize scan result")

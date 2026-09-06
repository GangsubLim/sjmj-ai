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
    """
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert 'TITLE="[audit] baseline vulnerabilities (${AUDIT_REF})"' in body
    assert "--label needs-triage" in body
    assert "audit-digest: " in body


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
    """
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert 'if [ "$AUDIT_REF" != "$DEFAULT_BRANCH" ]; then' in body
    assert 'if outcome != "success" and not rows:' in body

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


# --- osv-scan 트랙(PR ③) -------------------------------------------------------
# 위 freshness 불변식과 달리 osv-scan은 설치 잡의 선행 조건이 아니다(알려진 CVE 탐지이지
# install-time RCE 방어가 아니라서, 일회용 러너에 CVE 보유 패키지가 설치되는 것은 침해가
# 아니다). 강제력은 ruleset required 등록에서 나오고, 여기서는 그 잡이 실제로 무엇을
# 강제하도록 구성됐는지를 고정한다.

_OSV_REUSABLE = (
    "google/osv-scanner-action/.github/workflows/osv-scanner-reusable-pr.yml"
    "@6e4298ebc4db23e847df9b2e2de2939d6f066c67"
)
_DOWNLOAD_ARTIFACT = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"


def test_osv_scan_calls_pinned_reusable_workflow_with_fail_closed_inputs() -> None:
    """osv-scan은 SHA 핀 reusable workflow를 fail-on-vuln으로 호출해야 한다.

    fail-on-vuln이 빠지면 upstream 기본값에 의존하게 되어 SHA bump 한 번에 게이트가
    조용히 관측 트랙으로 강등된다. permissions 블록은 선택이 아니다 — 호출 대상이
    잡 레벨(jobs.osv-scan.permissions)에 security-events: write를 정적 선언하므로 caller가 같은 권한을 주지
    않으면 워크플로가 startup에서 거부된다(upload-sarif: false여도 정적 검증이 먼저다).

    scan-args의 명시 --lockfile 3축은 PR 게이트의 커버리지 하한이다. 이 인자가 빠지면 upstream
    기본값 `-r ./`로 되돌아가 한 축이 통째로 건너뛰어져도 게이트가 green이 되므로, 경로가
    실재하는지까지 여기서 고정한다.
    """
    job = _jobs()["osv-scan"]
    assert job["uses"] == _OSV_REUSABLE
    assert job["with"]["fail-on-vuln"] is True
    assert job["with"]["upload-sarif"] is False
    assert job["permissions"] == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }
    args = job["with"]["scan-args"].split()
    assert "--recursive" in args
    lockfiles = [a.split("=", 1)[1] for a in args if a.startswith("--lockfile=")]
    assert sorted(lockfiles) == [
        "apps/invoice-ocr/backend/uv.lock",
        "apps/invoice-ocr/frontend/package-lock.json",
        "apps/invoice-ocr/ml/uv.lock",
    ]
    missing = [path for path in lockfiles if not (_REPO_ROOT / path).is_file()]
    assert not missing, missing
    # 설치 잡 체인 금지(spec §3) — 체인은 CI 직렬화 비용만 남긴다.
    assert "needs" not in job


def test_osv_scan_assert_closes_the_scanner_fail_open() -> None:
    """스캐너가 조용히 green이 되는 경로를 caller 쪽에서 닫아야 한다.

    upstream의 "Run scanner on new code" step은 continue-on-error: true이고, 리포터는
    new-results.json을 못 읽으면 취약점 0건으로 간주해 diff를 공집합으로 만든다 → exit 0.
    needs + if: !cancelled() + 첫 step 명시 검증 + artifact 실물 검증 넷 중 하나라도
    빠지면 그 fail-open이 되살아나므로 함께 고정한다.

    다운로드 경로와 검증 경로의 연결, 그리고 파일 부재 분기의 존속도 같은 축으로 고정 — 한쪽만
    바뀌면 실제 CI만 깨지고 계약 테스트는 통과하는 거짓 green이 열림
    """
    job = _jobs()["osv-scan-assert"]
    assert job["needs"] == ["osv-scan"]
    assert job["if"] == "${{ !cancelled() }}"
    assert job["timeout-minutes"] == 5

    steps = {step.get("name"): step for step in job["steps"]}
    gate = steps["Verify prerequisite gates"]
    download = steps["Download new-code scan results"]
    verify = steps["Assert new-code scan produced results"]
    # 원시 텍스트로 매칭하면 실제 조건을 지우고 `#` 주석으로만 남겨도 통과하므로 한 번 정규화해 쓴다.
    gate_run = _strip_comments(gate["run"])
    verify_run = _strip_comments(verify["run"])
    # 조건이 붙으면 그 자리에서 게이트가 무력화되므로 무조건 실행을 함께 고정한다.
    assert "if" not in gate
    assert gate["env"]["OSV_RESULT"] == "${{ needs.osv-scan.result }}"
    assert '"$OSV_RESULT" = "success"' in gate_run

    assert download["uses"] == _DOWNLOAD_ARTIFACT
    # upstream이 같은 run에 올리는 이름. 스캔이 산출물을 못 만들면 upload-artifact가
    # if-no-files-found: warn 기본값으로 artifact를 만들지 않아 여기가 red가 된다.
    assert download["with"]["name"] == "new-json-results"

    assert '[ -s "$RESULTS" ]' in verify_run
    assert '.results | type == "array"' in verify_run

    # 다운로드 위치와 검증 경로의 연결. 리터럴을 두 번 적으면 한쪽만 바뀌어도 green이 되므로
    # 기대 문자열을 download의 path에서 만들어 대조한다.
    download_path = download["with"]["path"]
    assert f"RESULTS={download_path}/new-results.json" in verify_run
    assert download_path == "osv-assert"
    # 파일 부재(artifact 내부 파일명 계약 위반)와 빈 파일(스캐너 fail-open)을 가르는 분기.
    # 조치가 다른 두 원인이 다시 한 메시지로 뭉개지지 않게 부재 분기의 존속을 고정한다.
    assert '[ ! -f "$RESULTS" ]' in verify_run


# ==================== gitleaks 2-tier (PR ④) ====================
# 세 계층(pre-commit·PR 게이트·주기 baseline)이 같은 릴리스에 고정돼야 한다.
_GITLEAKS_VERSION = "8.30.1"
_GITLEAKS_SHA256 = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"


def _step(steps: list, name_fragment: str) -> dict:
    """step 목록에서 이름에 조각이 든 step 하나를 고른다(0건·2건 이상은 실패)."""
    matches = [step for step in steps if name_fragment in step.get("name", "")]
    assert len(matches) == 1, f"{name_fragment}: {[step.get('name') for step in steps]}"
    return matches[0]


def _env_values(text: str, key: str) -> list[str]:
    """워크플로 원문에서 `KEY: value` 형태 env 지정값을 등장 순서대로 모은다.

    잡·step 어느 수준에 놓였든 잡히도록 파싱 결과가 아니라 원문을 훑는다.
    """
    prefix = f"{key}:"
    return [
        line.strip()[len(prefix) :].strip()
        for line in text.splitlines()
        if line.strip().startswith(prefix)
    ]


def _assert_guard_exits(run: str, error_message: str) -> None:
    """`::error::` 진단 바로 다음 줄이 `exit 1`인지 확인한다.

    한 `run` 블록에 독립된 가드가 여럿이면 `"exit 1" in run` 단언은 어느 한 가드의
    `exit 1`이 지워져도 다른 가드 덕분에 통과한다 — 가드별로 좁혀야 락이 실효한다.

    Args:
        run: 주석을 제거한 step의 `run` 텍스트.
        error_message: 가드를 특정하는 `::error::` 진단의 앞부분.

    Raises:
        AssertionError: 진단이 없거나 그 다음 줄이 `exit 1`이 아닐 때.
    """
    assert error_message in run, error_message
    tail = run.split(error_message, 1)[1].splitlines()
    assert len(tail) > 1 and tail[1].strip() == "exit 1", error_message


def test_gitleaks_job_runs_unconditionally_and_scans_pr_range() -> None:
    """gitleaks 잡은 무조건 실행되고 PR 커밋 범위를 merge 커밋까지 포함해 스캔해야 한다.

    `needs`·`if`가 붙으면 선행 잡 실패 시 이 잡이 skipped가 되고, GitHub는 skipped
    required check를 success로 인정해 시크릿이 든 PR의 머지 버튼이 열린다.
    BASE_SHA가 비면 스캔 범위 계산이 불가능하므로 조용한 통과가 아니라 fail이어야 한다.
    `--diff-merges=first-parent`가 빠지면 `git log`가 merge 커밋 patch를 생략해
    충돌 해소 과정에 들어간 시크릿이 통째로 새어나간다(2026-09-06 실측).
    """
    job = _jobs()["gitleaks"]
    assert "needs" not in job
    assert "if" not in job
    assert job["timeout-minutes"] == 10, (
        "required check가 상한 없이 pending으로 매달리면 머지가 막힌다"
    )
    checkout = job["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0, "BASE..HEAD 접근에 전체 히스토리 필요"
    assert checkout["with"]["persist-credentials"] is False
    scan = _step(job["steps"], "Scan PR commit range")
    assert scan["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    run = _strip_comments(scan["run"])
    assert '[ -z "${BASE_SHA:-}" ]' in run
    _assert_guard_exits(run, "::error::BASE_SHA is empty")
    assert '--log-opts="${BASE_SHA}..HEAD --diff-merges=first-parent"' in run
    assert "--exit-code 1" in run, "기본값과 같으나 설정 드리프트 방지를 위해 명시 고정"
    assert "--redact" in run
    assert "--ignore-gitleaks-allow" in run, "`gitleaks:allow` 주석 우회 차단"


def test_gitleaks_job_fails_closed_when_git_traversal_breaks() -> None:
    """git 순회가 깨지면 초록이 아니라 빨강이어야 한다.

    gitleaks는 `fatal: Invalid revision range` 뒤에도 `no leaks found` + exit 0을
    낸다(v8.30.1 실측). base 강제푸시·객체 미fetch가 게이트를 조용히 무력화하므로
    범위 양 끝을 선검증하고, gitleaks stderr에 git 오류가 남으면 fail-closed다.
    """
    run = _strip_comments(_step(_jobs()["gitleaks"]["steps"], "Scan PR commit range")["run"])
    assert 'git rev-parse --verify --quiet "${rev}^{commit}"' in run
    _assert_guard_exits(run, "::error::${rev} is not a resolvable commit")
    assert "2> gitleaks-stderr.log" in run
    assert "grep -qE 'fatal:|stderr is not empty' gitleaks-stderr.log" in run
    _assert_guard_exits(run, "::error::gitleaks aborted git traversal")
    assert 'exit "$scan_status"' in run


def test_gitleaks_job_rejects_repo_controlled_suppressors() -> None:
    """스캔 대상 루트의 억제 파일은 존재 자체가 실패여야 한다.

    gitleaks는 `(target)/.gitleaks.toml`과 루트 `.gitleaksignore`를 자동 발견한다.
    같은 PR이 둘 중 하나를 얹으면 게이트가 통째로 무력화되고(실측 exit 1 → 0),
    `--config`로 레포 밖 설정을 강제해도 `.gitleaksignore`는 계속 적용된다(실측).
    """
    run = _strip_comments(
        _step(_jobs()["gitleaks"]["steps"], "Reject repo-controlled gitleaks suppression")["run"]
    )
    assert ".gitleaks.toml .gitleaksignore" in run
    assert "exit 1" in run


def test_gitleaks_job_steps_cannot_be_soft_failed() -> None:
    """잡·step 어느 수준에도 실패를 삼키는 스위치가 없어야 한다."""
    job = _jobs()["gitleaks"]
    assert "continue-on-error" not in job
    soft = [
        step.get("name", "?")
        for step in job["steps"]
        if "if" in step or "continue-on-error" in step
    ]
    assert not soft, soft


def test_gitleaks_job_pins_binary_by_version_and_checksum() -> None:
    """바이너리는 고정 버전 + 하드코딩 SHA256으로만 들어와야 한다.

    액션이 아니라 릴리스 tarball을 받으므로 SHA 핀 정책의 대체물이 이 체크섬 하나다.
    `releases/latest`로 흘러가거나 검증 전에 압축을 풀면 공급망 앵커가 사라진다.
    """
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert _env_values(text, "GITLEAKS_VERSION") == [_GITLEAKS_VERSION]
    assert _env_values(text, "GITLEAKS_SHA256") == [_GITLEAKS_SHA256]
    stripped_text = _strip_comments(text)
    assert "sha256sum -c -" in stripped_text
    assert 'tarball="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"' in stripped_text
    assert "releases/download/v${GITLEAKS_VERSION}/${tarball}" in stripped_text
    assert "releases/latest" not in text
    download = _strip_comments(
        _step(_jobs()["gitleaks"]["steps"], "Download and verify gitleaks")["run"]
    )
    assert download.index("curl") < download.index("sha256sum -c -") < download.index("tar -xzf")


def test_gitleaks_ci_run_blocks_take_no_template_interpolation() -> None:
    """gitleaks 잡의 run 블록은 `${{ }}` 보간을 직접 담지 않는다(PR ① 정책 회귀 방지)."""
    offenders = [
        step.get("name", "?")
        for step in _jobs()["gitleaks"]["steps"]
        if "${{" in step.get("run", "")
    ]
    assert not offenders, offenders


# 이 상수는 Task 2에서 처음 필요해 이 지점에 둔다(모듈 상단 import 블록과 무관 — ruff E402 대상 아님).
_GITLEAKS_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "gitleaks-scheduled.yml"


def _scheduled() -> dict:
    """주기 스캔 워크플로를 파싱한다."""
    return yaml.safe_load(_GITLEAKS_WORKFLOW.read_text(encoding="utf-8"))


def test_scheduled_scan_covers_full_history_and_stays_non_blocking() -> None:
    """주기 스캔은 전체 히스토리를 보고, 검출돼도 잡을 실패시키지 않아야 한다.

    shallow clone에서 돌면 과거 커밋의 시크릿이 조용히 안 잡힌다 — baseline의 존재
    이유가 사라지므로 shallow면 명시적 fail이다. 반대로 검출 시 잡을 red로 만들면
    devel·main push마다 실패 알림만 쌓이고 이슈 upsert step에 도달하지 못한다.
    `--all`은 gitleaks 기본 순회(전 ref)와 동일하고 `--diff-merges=first-parent`가
    기본 생략되는 merge 커밋 patch를 덮는다.
    트리거 맵의 키가 `"on"`이 아니라 `True`인 것은 YAML 1.1이 `on`을 불리언으로 읽기 때문이다.
    """
    workflow = _scheduled()
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "0 1 * * *"}], "audit(00:00 UTC)와 시간 분리"
    assert triggers["push"]["branches"] == ["devel", "main"]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    steps = workflow["jobs"]["scan"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert steps[0]["with"]["persist-credentials"] is False
    # 원문 substring 단언은 대상 줄을 `#` 주석으로 강등해도 통과하는 거짓 green을 낳는다
    # (이 결함이 PR ② `3361f05`·PR ③ `113f0c5`·Task 1 `f016758`에서 세 번 검출됐다) —
    # positive 단언은 전부 `_strip_comments()` 통과 문자열에 대해 한다.
    shallow = _strip_comments(_step(steps, "Verify full history")["run"])
    assert "git rev-parse --is-shallow-repository" in shallow
    assert "exit 1" in shallow
    scan_step = _step(steps, "Scan full history")
    assert scan_step["id"] == "scan"
    scan = _strip_comments(scan_step["run"])
    assert "set +e" in scan
    assert 'echo "exit_code=$scan_status"' in scan
    assert "--report-format json" in scan
    assert "--exit-code 1" in scan
    assert "--ignore-gitleaks-allow" in scan, "per-PR 게이트와 동일 우회 차단 정책"
    assert '--log-opts="--all --diff-merges=first-parent"' in scan


def test_scheduled_incomplete_scan_fails_the_job() -> None:
    """스캔 미완주는 "검출 0"과 구별돼 잡을 실패시켜야 한다.

    구별하지 않으면 git 오류로 인한 exit 0이 close 경로를 태워 실재 findings 이슈에
    "clean"이라 코멘트하고 닫는다 — baseline 알림의 silent 소실이다.

    gitleaks가 의미를 부여한 종료 코드는 0(clean)·1(검출) 둘뿐이라, 그 밖의 코드로 끝나면서
    리포트 파일을 남기고 stderr가 두 패턴에 걸리지 않는 런은 미완주의 부분 결과를
    "검출됨"으로 이슈에 올리는 경로 — 종료 코드 제약도 같은 축으로 함께 고정
    """
    steps = _scheduled()["jobs"]["scan"]["steps"]
    scan = _strip_comments(_step(steps, "Scan full history")["run"])
    assert 'echo "scan_ok=$scan_ok"' in scan
    assert 'case "$scan_status" in' in scan, "gitleaks 종료 코드 {0,1} 밖은 미완주로 다룬다"
    assert "*) scan_ok=false ;;" in scan
    assert "grep -qE 'fatal:|stderr is not empty' gitleaks-stderr.log" in scan
    assert "[ ! -f gitleaks-report.json ]" in scan
    guard = _step(steps, "Fail when the scan did not complete")
    assert guard["if"] == "steps.scan.outputs.scan_ok != 'true'"
    assert "exit 1" in _strip_comments(guard["run"])
    names = [step.get("name", "") for step in steps]
    assert names.index("Fail when the scan did not complete") < names.index(
        "Close tracking issue when clean"
    )


def test_scheduled_steps_cannot_be_soft_failed() -> None:
    """실패를 삼키는 스위치가 잡·step 어느 수준에도 없어야 한다.

    `Fail when the scan did not complete`에 `continue-on-error: true`가 붙으면 잡이
    계속 진행해 `Close tracking issue when clean`(`exit_code == '0'`)에 도달하고,
    스캔이 깨져 exit 0을 낸 런이 실재 findings 이슈를 "clean"으로 닫는다 —
    이 워크플로가 막으려는 silent-close가 그대로 되살아난다.
    """
    job = _scheduled()["jobs"]["scan"]
    assert job["timeout-minutes"] == 30, "히스토리 성장에 따라 전체 스캔에 상한이 필요하다"
    assert "continue-on-error" not in job
    soft = [step.get("name", "?") for step in job["steps"] if "continue-on-error" in step]
    assert not soft, soft


def test_scheduled_rejects_repo_controlled_suppressors() -> None:
    """baseline도 PR 게이트와 같은 억제 파일 정책을 적용해야 한다."""
    steps = _scheduled()["jobs"]["scan"]["steps"]
    run = _strip_comments(_step(steps, "Reject repo-controlled gitleaks suppression")["run"])
    assert ".gitleaks.toml .gitleaksignore" in run
    assert "exit 1" in run


def test_scheduled_issue_upsert_uses_repo_label_and_hides_secrets() -> None:
    """이슈 본문에 시크릿 원문·라인 좌표가 실리면 안 되고, 라벨은 이 레포에 실존해야 한다.

    fs-web 원문의 `security` 라벨은 sjmj-ai에 없어 `gh issue create`가 실패한다
    (spec D12 → `needs-triage`). 요약은 RuleID/File/Commit만 담고 `.Secret`·`.Match`·
    `.Fingerprint`는 어디에서도 참조하지 않는다 — public 레포의 공개 이슈다.
    제목은 ref를 담지 않는 레포 단일 키다(`gitleaks git`이 전 ref를 순회하므로
    devel·main 런이 같은 findings를 본다).

    부재(negative) 단언(`--label security`·`.Secret`·`.Match`·`.Fingerprint`)은 원문
    그대로 본다 — 주석 안에 등장해도 실제 유출 표면이 되므로 stripped 텍스트로 완화하면
    안 된다.
    """
    workflow = _scheduled()
    text = _GITLEAKS_WORKFLOW.read_text(encoding="utf-8")
    env = workflow["jobs"]["scan"]["env"]
    assert env["ISSUE_TITLE"] == "[gitleaks] baseline secret findings"
    steps = workflow["jobs"]["scan"]["steps"]
    upsert = _step(steps, "Open or update tracking issue")
    assert upsert["if"] == "steps.scan.outputs.exit_code != '0'"
    upsert_run = _strip_comments(upsert["run"])
    assert "--label needs-triage" in upsert_run
    assert "--label security" not in text
    assert ".Secret" not in text
    assert ".Match" not in text
    assert ".Fingerprint" not in text
    assert "--limit 100" in upsert_run, "기본 30건 창 밖이면 중복 생성"
    assert 'jq -r --arg title "$ISSUE_TITLE"' in upsert_run, "제목을 jq 식에 보간하지 않음"
    assert "--redact" in _strip_comments(_step(steps, "Scan full history")["run"])
    close = _step(steps, "Close tracking issue when clean")
    assert close["if"] == "steps.scan.outputs.exit_code == '0'"
    close_run = _strip_comments(close["run"])
    assert 'nums="$(gh issue list' in close_run, "조회 실패를 삼키지 않게 대입문으로 분리"
    assert "for num in $nums" in close_run


def test_scheduled_runs_are_serialized_not_cancelled() -> None:
    """이슈 키가 레포 단일이므로 동시 실행은 취소가 아니라 직렬화여야 한다."""
    assert _scheduled()["concurrency"] == {
        "group": "gitleaks-scheduled",
        "cancel-in-progress": False,
    }


def test_scheduled_pins_the_same_gitleaks_binary() -> None:
    """주기 스캔도 PR 게이트와 같은 릴리스·같은 체크섬으로 고정돼야 한다.

    두 계층이 다른 버전을 쓰면 PR에서 통과한 룰셋과 baseline 룰셋이 갈려
    baseline 이슈가 PR 게이트로 재현되지 않는다.

    curl < sha256sum -c - < tar -xzf 순서도 PR 게이트(Task 1,
    `test_gitleaks_job_pins_binary_by_version_and_checksum`)와 동일하게 고정한다 —
    존재·부재만 보면 `tar -xzf`를 검증 앞으로 옮겨 미검증 바이너리를 푸는 회귀를
    잡지 못한다. 이 워크플로는 `issues: write`와 `GH_TOKEN`을 들고 도는 잡이라
    영향이 PR 게이트보다 작지 않다.

    두 계층의 download 블록 전문 대조는 존재 단언이 놓치는 드리프트까지 차단 — env 상수만
    같은 버전으로 남고 tarball 이름·다운로드 URL이 다른 버전으로 갈려도 개별 존재 단언은
    통과하므로, 블록 자체의 동일성을 마지막 앵커로 고정
    """
    text = _GITLEAKS_WORKFLOW.read_text(encoding="utf-8")
    assert _env_values(text, "GITLEAKS_VERSION") == [_GITLEAKS_VERSION]
    assert _env_values(text, "GITLEAKS_SHA256") == [_GITLEAKS_SHA256]
    stripped_text = _strip_comments(text)
    assert 'tarball="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"' in stripped_text
    assert "releases/download/v${GITLEAKS_VERSION}/${tarball}" in stripped_text
    assert "sha256sum -c -" in stripped_text
    assert "releases/latest" not in text
    download = _strip_comments(
        _step(_scheduled()["jobs"]["scan"]["steps"], "Download and verify gitleaks")["run"]
    )
    assert download.index("curl") < download.index("sha256sum -c -") < download.index("tar -xzf")
    ci_download = _strip_comments(
        _step(_jobs()["gitleaks"]["steps"], "Download and verify gitleaks")["run"]
    )
    assert download == ci_download, "두 계층의 download 블록이 드리프트하면 버전·검증이 갈린다"


def test_scheduled_run_blocks_take_no_template_interpolation() -> None:
    """주기 스캔의 run 블록도 `${{ }}`를 직접 담지 않는다(보간은 env로만).

    이 부재 단언은 원문(unstripped) 그대로 본다 — GitHub Actions는 `run:` 블록 안의
    `${{ }}`를 셸 실행 전에 치환하며 그 치환은 `#` 주석 라인 안에서도 일어나므로,
    `_strip_comments()`를 거치면 주석에 숨은 보간을 놓친다.
    """
    offenders = [
        step.get("name", "?")
        for step in _scheduled()["jobs"]["scan"]["steps"]
        if "${{" in step.get("run", "")
    ]
    assert not offenders, offenders


# pre-commit 계층은 Task 3에서 합류한다.
_PRE_COMMIT_CONFIG = _REPO_ROOT / ".pre-commit-config.yaml"
# pre-commit은 태그가 아니라 커밋 SHA로 고정한다 — v8.30.1이 가리키는 커밋.
_GITLEAKS_PRE_COMMIT_REV = "83d9cd684c87d95d656c1458ef04895a7f1cbd8e"


def test_pre_commit_hook_pins_the_same_gitleaks_release() -> None:
    """로컬 1차 차단 계층도 CI 두 계층과 같은 v8.30.1에 고정돼야 한다.

    태그(`v8.30.1`)는 옮겨 달 수 있으므로 rev는 커밋 SHA이고, 사람이 읽는
    `# frozen: vX` 주석이 그 SHA가 어느 릴리스인지 남긴다. `--ignore-gitleaks-allow`가
    빠지면 개발자가 `gitleaks:allow` 주석만으로 로컬 훅을 무력화할 수 있다.
    """
    text = _PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    entries = [
        repo for repo in config["repos"] if repo["repo"] == "https://github.com/gitleaks/gitleaks"
    ]
    assert len(entries) == 1, [repo["repo"] for repo in config["repos"]]
    entry = entries[0]
    assert entry["rev"] == _GITLEAKS_PRE_COMMIT_REV
    assert entry["hooks"] == [{"id": "gitleaks", "args": ["--ignore-gitleaks-allow"]}]
    assert f"{_GITLEAKS_PRE_COMMIT_REV} # frozen: v{_GITLEAKS_VERSION}" in text

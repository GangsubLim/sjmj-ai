"""빈 크롭 자동 배제 — 캘리브레이션 리포트 + 운영 DB 반영 도구 (Issue #38, ADR 0006).

배포 서버(macmini)의 training_pairs와 품목 크롭 PNG를 로컬 캐시로 동기화해, 크롭 잉크율
분포와 판정을 전수 산출하고(report) 확정된 임계로 운영 DB에 자동 배제를 반영한다(apply).
쓰기를 별 커맨드로 뗀 이유는 운영 DB 사고 방지다 — report를 눈으로 보고 apply를 친다.

계층 분리는 warp_gate_report와 같다: 순수 계층(파싱·SQL 조립·계획·결과 해석)은 단위테스트
대상이고, ssh/mysql 글루는 비대상이다. 캘리브레이션 축(라벨 manifest·마진·리포트 렌더)은
`tools.blank_crop_calib`, 원격→캐시 동기화는 `tools.cache_sync`(warp_gate_report와 공유)에
있다.

Usage:
    uv run python -m tools.blank_crop_report fetch
    uv run python -m tools.blank_crop_report report --labels <labels.csv>
    uv run python -m tools.blank_crop_report apply --dry-run
    uv run python -m tools.blank_crop_report apply [--recheck-reviewed] [--allow-holds]
"""

import argparse
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from handwriting.blank_crop import (
    BLANK_CROP,
    STATUS_EXCLUDED,
    STATUS_INCLUDED,
    is_blank,
    is_machine_writable,
)
from tools.blank_crop_calib import (
    HOLD_STATUSES,
    STATUS_OK,
    crop_status,
    load_labels,
    read_labels_csv,
    render_blank_report,
)
from tools.cache_sync import (
    invalidate_manifest,
    load_cache_meta,
    sync_remote_files,
    write_manifest,
)
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    env_or,
    mysql_script,
    run_ssh,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "blank_crop"

PAIRS_NAME = "pairs.json"
CROP_GLOB = "job-*/row-*.png"

# meta.json에 fetch가 남기는 쓰기 대상 신원 — apply가 같은 키로 대조한다(H2 확장).
META_BACKEND_ENV = "backend_env"

PAIRS_SQL = (
    "SELECT tp.id, tp.crop_ref, tp.job_id, tp.status, tp.exclusion_reason, j.curation_reviewed "
    "FROM training_pairs tp JOIN ocr_jobs j ON j.id = tp.job_id "
    "ORDER BY tp.job_id, tp.row_index"
)

# SQL 리터럴로 나갈 수 있는 값의 닫힌 집합 — 축별로 따로 닫는다(사유 값이 status 자리에
# 들어가도 SQL은 통과하지만 학습쌍 상태가 알 수 없는 값으로 뒤집히기 때문).
ALLOWED_PAIR_STATUS = (STATUS_INCLUDED, STATUS_EXCLUDED)
ALLOWED_EXCLUSION_REASON = (None, BLANK_CROP)

# crop_ref는 경로 조립과 마크다운 셀로 동시에 나간다 — 두 문을 이 한 줄로 닫는다(M6).
CROP_REF_RE = re.compile(r"job-\d+/row-\d+")

# 재처리가 만드는 비-크롭 좌표(handwriting/relink.py의 tmp_ref·orphan_ref). 이 쌍들은
# 가리키는 PNG가 없어 잉크율 판정 자체가 성립하지 않으므로 리포트 모수에서 뺀다.
# PAIRS_SQL이 training_pairs 전수를 읽는 이상 여기서 걸러야 한다 — 형식 위반으로
# raise하면 미결 쌍 1건에 도구 전체가 fetch에서 죽는다.
NON_CROP_REF_RE = re.compile(r"job-\d+/(?:tmp|orphan)-\d+")

# 조립된 스크립트는 통째로 `mysql ... -e <one arg>`에 실려 ssh argv로 나간다. macOS
# ARG_MAX는 1MiB이고 거기엔 환경변수와 나머지 argv도 함께 들어가므로 1/4을 상한으로 둔다
# (쌍당 약 210B → 대략 1,200쌍). 청크 분할은 트랜잭션 원자성을 깨므로 하지 않는다(M5).
MAX_APPLY_SCRIPT_BYTES = 256 * 1024


def _cell(value: str) -> str | None:
    return None if value == "NULL" else value


def _crop_ref(value: str) -> str:
    """DB의 crop_ref를 `job-<n>/row-<m>` 형태로 좁힌다(M6).

    이 값은 두 문으로 동시에 나간다 — `crop_path`가 `cache/crops/<ref>.png`로 경로를
    조립하고(`../`면 캐시 밖 파일의 잉크율로 판정이 갈린다), 리포트 표에 셀로 그대로
    실린다(`|`·개행이 표를 깨뜨린다). 정규식 하나로 두 문을 함께 닫는다.

    Raises:
        ValueError: 형태가 어긋날 때.
    """
    if not CROP_REF_RE.fullmatch(value):
        raise ValueError(f"crop_ref 형태가 아니다: {value!r} — 'job-<n>/row-<m>'만 허용")
    return value


def parse_pairs_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(training_pairs + 잡 검수 표식)를 dict 리스트로 파싱한다.

    재처리 미결·임시 좌표(`orphan-`·`tmp-`)를 가진 쌍은 건너뛴다 — 크롭 파일이 없어
    이 도구가 판정할 대상이 아니다. 그 밖의 형식 위반은 그대로 거부한다.

    Raises:
        ValueError: id 축이 정수가 아니거나 crop_ref가 `job-<n>/row-<m>` 형태가 아닐 때.
    """
    lines = text.strip().split("\n")
    header = lines[0].split("\t")
    out = []
    for ln in lines[1:]:
        if not ln.strip():
            continue
        d = dict(zip(header, ln.split("\t"), strict=True))
        if NON_CROP_REF_RE.fullmatch(d["crop_ref"]):
            continue
        out.append(
            {
                "id": int(d["id"]),
                "crop_ref": _crop_ref(d["crop_ref"]),
                "job_id": int(d["job_id"]),
                "pair_status": d["status"],
                "exclusion_reason": _cell(d["exclusion_reason"]),
                "curation_reviewed": d["curation_reviewed"] == "1",
            }
        )
    return out


def crop_path(cache: Path, crop_ref: str) -> Path:
    """crop_ref('job-42/row-0')에 대응하는 캐시 PNG 경로."""
    return cache / "crops" / f"{crop_ref}.png"


@dataclass(frozen=True)
class PairUpdate:
    """조건부 UPDATE 1건 — 캐시에서 본 상태(seen)와 목표 상태(new)를 함께 싣는다."""

    pair_id: int
    job_id: int
    seen_status: str
    seen_reason: str | None
    new_status: str
    new_reason: str | None


def select_targets(records: list[dict], *, recheck_reviewed: bool) -> list[dict]:
    """잡 단위 가드 — 검수 완료된 잡은 기계가 손대지 않는다(--recheck-reviewed로 해제)."""
    if recheck_reviewed:
        return list(records)
    return [r for r in records if not r["curation_reviewed"]]


def plan_updates(records: list[dict], threshold: float) -> tuple[list[PairUpdate], dict[str, int]]:
    """판정을 §6 불변식에 통과시켜 조건부 UPDATE 계획으로 바꾼다.

    측정 축(`crop_status`)과 DB 축(`pair_status`)은 다른 축이다 — 보류 게이트는 전자로,
    조건부 UPDATE의 WHERE에 실리는 seen 상태는 후자로 판단한다.

    보류(잉크율 없음)는 계획에서 빠지고 DB에서 기존 상태 그대로 남는다 — '보류'라는
    세 번째 상태값은 존재하지 않는다(spec §8). 목표 상태가 캐시에서 본 상태와 같은 쌍에는
    UPDATE를 쏘지 않는다: MySQL의 affected row는 실제로 바뀐 행 수라 no-op도 0을 내므로,
    안 쏘는 쌍을 걸러야 affected 0이 언제나 충돌을 뜻하게 된다.

    Args:
        records: `select_targets`를 통과한 집합이어야 한다(M3) — 잡 단위 검수완료 가드는
            여기서 다시 걸지 않는다. 순서를 빠뜨리면 검수 완료 잡을 기계가 조용히 쓴다.
            그 외엔 fetch/측정이 끝난 레코드(`crop_status`·`ratio`·`pair_status`·
            `exclusion_reason`·`id`·`job_id`).
        threshold: 확정된 `BLANK_INK_MAX`.

    Returns:
        (계획, {"protected": 사람 판정이라 건드리지 않은 수, "unchanged": 이미 목표 상태인 수})
    """
    updates: list[PairUpdate] = []
    counts: dict[str, int] = {"protected": 0, "unchanged": 0}
    for r in records:
        if r["crop_status"] != STATUS_OK or r["ratio"] is None:
            continue
        seen_status, seen_reason = r["pair_status"], r["exclusion_reason"]
        if not is_machine_writable(seen_status, seen_reason):
            counts["protected"] += 1
            continue
        if is_blank(r["ratio"], threshold):
            new_status, new_reason = STATUS_EXCLUDED, BLANK_CROP
        else:
            # 기계가 자기 판정을 취소할 때 사유를 반드시 NULL로 지운다 — 남기면
            # '사람이 되돌림' 칸과 구분되지 않아 자기 정정 결과를 영구 보호해버린다.
            new_status, new_reason = STATUS_INCLUDED, None
        if (new_status, new_reason) == (seen_status, seen_reason):
            counts["unchanged"] += 1
            continue
        updates.append(
            PairUpdate(
                pair_id=r["id"],
                job_id=r["job_id"],
                seen_status=seen_status,
                seen_reason=seen_reason,
                new_status=new_status,
                new_reason=new_reason,
            )
        )
    return updates, counts


@dataclass(frozen=True)
class ApplyPlan:
    """apply 한 회차의 계획 — 대상·보류·UPDATE 계획·집계."""

    targets: list[dict]
    holds: list[dict]
    updates: list[PairUpdate]
    counts: dict[str, int]


def plan_apply(records: list[dict], threshold: float, *, recheck_reviewed: bool) -> ApplyPlan:
    """잡 단위 가드 → 보류 분리 → UPDATE 계획 순서를 코드로 고정한다(M3).

    `plan_updates`는 `curation_reviewed`를 보지 않는다 — 검수 완료 잡 가드는 오직
    `select_targets`를 **먼저** 부르는 것으로만 성립한다. 순서를 호출자 기억에 맡기면
    빠뜨렸을 때 아무 증상 없이 검수 완료 잡을 기계가 쓴다. 보류도 전 레코드가 아니라
    선택된 대상에서만 센다(기본 실행이 안 건드릴 잡의 보류로 게이트를 세우지 않는다).

    Args:
        records: `evaluate_cached`가 만든 측정 결과 전수.
        threshold: 확정된 `BLANK_INK_MAX`.
        recheck_reviewed: True면 검수 완료 잡까지 대상에 포함한다.

    Returns:
        ApplyPlan — 이 회차의 대상·보류·UPDATE 계획·집계.
    """
    targets = select_targets(records, recheck_reviewed=recheck_reviewed)
    holds = [r for r in targets if r["crop_status"] in HOLD_STATUSES]
    updates, counts = plan_updates(targets, threshold)
    return ApplyPlan(targets=targets, holds=holds, updates=updates, counts=counts)


def _status_lit(value: str) -> str:
    """pair status를 SQL 리터럴로 만든다(닫힌 집합 밖이면 즉시 실패 — 주입 표면 없음)."""
    if value not in ALLOWED_PAIR_STATUS:
        raise ValueError(f"허용되지 않은 status 리터럴: {value!r} — {ALLOWED_PAIR_STATUS}")
    return f"'{value}'"


def _reason_lit(value: str | None) -> str:
    """exclusion_reason을 SQL 리터럴로 만든다(NULL 포함 닫힌 집합)."""
    if value not in ALLOWED_EXCLUSION_REASON:
        raise ValueError(f"허용되지 않은 사유 리터럴: {value!r} — {ALLOWED_EXCLUSION_REASON}")
    return "NULL" if value is None else f"'{value}'"


def _id_lit(value: int) -> str:
    """pair_id/job_id를 SQL 리터럴로 만든다(H1 — 정수 축도 문자열 축과 같이 닫는다).

    `PairUpdate`의 `int` 타입힌트는 런타임에 강제되지 않는다 — 현재 유일한 생산 경로가
    `parse_pairs_tsv`의 `int()`라 도달 불가일 뿐, 검증을 호출자 기억에만 맡기면 방어가
    아니다. `bool`은 `int`의 서브클래스라 `isinstance(value, int)`만으로는 걸러지지
    않으므로 별도로 배제한다.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"허용되지 않은 id 리터럴: {value!r} — int만 허용")
    return str(value)


def build_apply_script(updates: list[PairUpdate]) -> str:
    """조건부 UPDATE + ROW_COUNT 프로브 + 잡 검수표식 되돌림을 한 트랜잭션으로 조립한다.

    각 UPDATE는 캐시에서 본 상태를 WHERE에 실어 fetch~apply 사이의 사람 PATCH를 덮지 않는다
    (파이썬 술어는 SELECT한 행에만 적용되므로 WHERE에 재표명하지 않으면 그 사이에 들어온
    사람 PATCH를 덮는다). exclusion_reason 비교는 항상 `<=>`다 — `= NULL`은 항상 거짓이라
    사유가 NULL인 쌍이 전부 조용히 0행이 된다.

    잡 검수표식은 **미처리 쌍이 남은 잡만** 되돌린다(EXISTS로 SQL 안에서 유도) — spec §4③의
    "판정이 바뀐 쌍을 가진 잡"을 근사 없이 충족한다. 조건부 UPDATE가 실제로 성공한 쌍에만
    reviewed_at = NULL을 쓰므로, 뒤 시점의 '미처리 쌍이 있는 잡'은 곧 '판정이 바뀐 쌍이
    있는 잡'이다. 충돌만 있고 변경 0인 잡은 조건이 거짓이 되어 표식이 유지되므로
    '미검수 + 미처리 0'(사람이 "볼 것 없음"으로 읽는 상태)이 만들어지지 않는다.

    ROW_COUNT 프로브는 전부 COMMIT 앞에서 출력된다(M4) — 교착·연결 끊김으로 COMMIT 자체가
    실패해도 stdout엔 이미 `affected 1`이 찍혀 있어, 출력만 보면 "적용 성공"처럼 읽힐 수
    있다. 원자성 자체는 문제없다(부분 적용 없음). **Task 11이 지켜야 할 계약**: apply 글루는
    반드시 `tools/remote.py`의 `run_ssh`를 경유해 mysql 비-0 종료를 `RemoteError`로 올려야
    하고, stdout 파싱 결과(`parse_apply_output`/`classify_affected`)만으로 성공 판정하지
    말 것. sentinel(예: `SELECT 'committed'`)을 추가하면 이 문제를 없앨 수 있지만 파서와
    동시 수정이 필요해 커플링이 생기므로 여기서는 하지 않는다.

    Returns:
        실행 가능한 SQL 스크립트. 계획이 비면 빈 문자열(쏠 것이 없다).

    Raises:
        ValueError: status·사유가 닫힌 집합 밖이거나, pair_id/job_id가 int가 아닐 때(H1) ·
            조립 결과가 `MAX_APPLY_SCRIPT_BYTES`를 넘을 때(M5).
    """
    if not updates:
        return ""
    parts = ["START TRANSACTION;"]
    for u in updates:
        pair_id_lit = _id_lit(u.pair_id)
        parts.append(
            "UPDATE training_pairs SET "
            f"status = {_status_lit(u.new_status)}, "
            f"exclusion_reason = {_reason_lit(u.new_reason)}, "
            "reviewed_at = NULL "
            f"WHERE id = {pair_id_lit} AND status = {_status_lit(u.seen_status)} "
            f"AND exclusion_reason <=> {_reason_lit(u.seen_reason)};"
        )
        parts.append(f"SELECT {pair_id_lit} AS pair_id, ROW_COUNT() AS affected;")
    job_id_set = {u.job_id for u in updates}
    for jid in job_id_set:
        _id_lit(jid)  # sorted()가 mixed-type 비교로 TypeError를 내기 전에 먼저 검증한다
    job_ids = ", ".join(str(j) for j in sorted(job_id_set))
    parts.append(
        "UPDATE ocr_jobs SET curation_reviewed = FALSE "
        f"WHERE id IN ({job_ids}) AND EXISTS ("
        "SELECT 1 FROM training_pairs tp "
        "WHERE tp.job_id = ocr_jobs.id AND tp.reviewed_at IS NULL);"
    )
    parts.append("COMMIT;")
    script = "\n".join(parts) + "\n"
    size = len(script.encode())
    if size > MAX_APPLY_SCRIPT_BYTES:
        raise ValueError(
            f"apply 스크립트가 상한을 넘었다({size}B > {MAX_APPLY_SCRIPT_BYTES}B, "
            f"쌍 {len(updates)}건) — 이 스크립트는 `mysql -e <one arg>`로 실려 ssh argv와 "
            "원격 셸 argv를 두 번 통과하므로 ARG_MAX(1MiB)에 걸린다. 청크로 쪼개면 "
            "트랜잭션 원자성이 깨지므로 쪼개지 말 것: SQL을 argv가 아닌 stdin으로 "
            "보내야 한다(run_ssh를 stdin 경유로 바꾸고 mysql도 heredoc으로 먹인다)."
        )
    return script


def parse_apply_output(text: str) -> dict[int, int]:
    """ROW_COUNT 프로브 출력(TSV, 결과셋마다 헤더 반복)을 pair_id→affected로 파싱한다.

    엄격 파싱을 택했다(M1) — 선례 `tools/bank_update.py`의 `parse_reviewed_job_ids`는
    관용 파싱(`isdigit`으로 조용히 skip)이지만, 거긴 단순 조회고 여긴 실제 운영 DB에
    반영된 UPDATE의 결과 확인이라 조용한 skip이 곧 거짓 안심("변경 성공")으로 직결된다.

    Raises:
        ValueError: 프로브 형식(`숫자\\t숫자`)이 아닌 줄을 만났을 때(경고 줄 혼입,
            `--batch` 누락 등 — 원문 줄을 메시지에 실어 원인을 짚게 한다) · 같은
            pair_id가 두 번 이상 나올 때(M2 — stale/잘린 출력 혼입을 조용히 덮지 않는다).
    """
    out: dict[int, int] = {}
    for raw in text.strip().split("\n"):
        line = raw.strip()
        if not line or line.startswith("pair_id"):
            continue
        pid, _, affected = line.partition("\t")
        try:
            parsed_pid, parsed_affected = int(pid), int(affected)
        except ValueError as e:
            raise ValueError(f"apply 출력 해석 실패 — 프로브 형식이 아닌 줄: {raw!r}") from e
        if parsed_pid in out:
            raise ValueError(
                f"apply 출력에 pair_id 중복: {parsed_pid} — stale 출력이 섞였을 수 있다"
            )
        out[parsed_pid] = parsed_affected
    return out


def classify_affected(updates: list[PairUpdate], affected: dict[int, int]) -> dict[str, list[int]]:
    """affected row를 변경/충돌/미지로 가른다 — 안 쏜 쌍을 걸렀으므로 0은 언제나 충돌이다.

    프로브 자체가 없는 쌍(스크립트 중단 등)도 적용됐다고 볼 근거가 없어 충돌로 센다.

    unknown(M2): 계획(updates)에 없는 pair_id가 프로브에 나타난 경우다 — 지금까진 조용히
    버려져 stale/잘린 출력이 섞여도 "충돌 0"이라는 거짓 안심이 나왔다.
    """
    planned_ids = {u.pair_id for u in updates}
    changed = [u.pair_id for u in updates if affected.get(u.pair_id, 0) > 0]
    conflict = [u.pair_id for u in updates if affected.get(u.pair_id, 0) <= 0]
    unknown = sorted(pid for pid in affected if pid not in planned_ids)
    return {"changed": changed, "conflict": conflict, "unknown": unknown}


def apply_exit_code(
    holds: list[dict],
    conflicts: Sequence[int],
    *,
    allow_holds: bool,
    unknown: Sequence[int] = (),
) -> int:
    """보류 또는 충돌(계획 밖 id 포함)이 있으면 비-0으로 끝난다.

    런북에서 apply는 bank_update의 앞 단계이므로(spec §5), 여기서 성공으로 끝나면 운영자가
    부분 적용 상태로 뱅크 갱신에 넘어간다. 보류는 --allow-holds로 의도적 무시가 가능하지만
    충돌·unknown에는 우회 플래그를 두지 않는다(M2) — 정확한 해소는 재-fetch 후 재실행이고,
    도구가 멱등이라 재실행 비용이 낮다(spec §8).
    """
    if conflicts or unknown:
        return 1
    if holds and not allow_holds:
        return 1
    return 0


def fetch_warnings(*, n_pairs: int, n_crops: int) -> list[str]:
    """fetch 결과가 '조용한 빈 결과'인지 판별한다(L4).

    `parse_pairs_tsv("")`는 `[]`를 돌려주므로 원격 질의가 빈 결과를 주면 리포트가 "학습쌍
    0"으로 태연히 렌더된다 — 잘못된 DB를 봤다는 사실이 어디에도 안 남는다. 크롭 0도 같은
    모양으로 전 쌍을 crop_missing 보류로 만든다. 두 축은 원인이 다르므로 따로 말한다
    (warp_gate_report.fetch_all의 fetch 가드 선례).
    """
    warnings = []
    if n_pairs == 0:
        warnings.append(
            "⚠️  training_pairs가 0건이다 — 원격 백엔드 env(DB_NAME 등)를 확인할 것. "
            "리포트는 '학습쌍 0'으로 태연히 렌더된다."
        )
    if n_crops == 0:
        warnings.append(
            "⚠️  품목 크롭 PNG가 0건이다 — 원격 워커 env(SJMJ_DATA_DIR)를 확인할 것. "
            "리포트는 전 쌍을 crop_missing 보류로 찍는다."
        )
    return warnings


# ---------------------------------------------------------------------------
# ssh/mysql 글루 + CLI (원격 접속 — 글루 자체는 단위테스트 비대상, 배선만 고정한다)
# ---------------------------------------------------------------------------


def fetch_all(host: str, backend_env: str, worker_env: str, cache: Path) -> dict:
    """training_pairs 전수와 품목 크롭 PNG를 캐시로 동기화한다(DB 쓰기 없음)."""
    cache.mkdir(parents=True, exist_ok=True)
    # 중단 시 '새 크롭 + 옛 meta'라는 하이브리드 캐시가 남지 않도록 먼저 무효화한다(M2).
    invalidate_manifest(cache, PAIRS_NAME)
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL)).decode())
    names = sync_remote_files(host, worker_env, pattern=CROP_GLOB, dest=cache / "crops")
    meta = write_manifest(
        cache,
        PAIRS_NAME,
        pairs,
        host=host,
        counts={"n_pairs": len(pairs), "n_crops": len(names)},
        # 쓰기 대상은 호스트만이 아니라 backend env(→ DB_HOST/DB_NAME)가 정한다 —
        # apply가 대조할 수 있도록 fetch 시점의 값을 남긴다(require_same_target).
        extra={META_BACKEND_ENV: backend_env},
    )
    for line in fetch_warnings(n_pairs=meta["n_pairs"], n_crops=meta["n_crops"]):
        print(line)
    return meta


def evaluate_cached(cache: Path, *, imread: Callable[[str], object] | None = None) -> list[dict]:
    """캐시된 크롭에 잉크율을 매겨 record 리스트를 만든다.

    Args:
        cache: fetch가 채운 캐시 디렉터리.
        imread: 이미지 리더 주입구(테스트의 Fake 어댑터). 기본값 None이면 이때만 cv2를
            import해 `cv2.imread`를 쓴다 — 덕분에 이 평가 경로를 코어 venv에서도 테스트한다.

    Returns:
        pairs.json의 각 쌍에 `crop_status`·`ratio`를 더한 record 리스트.
    """
    from handwriting.blank_crop import crop_ink_ratio

    if imread is None:
        import cv2

        imread = cv2.imread

    pairs = json.loads((cache / PAIRS_NAME).read_text(encoding="utf-8"))
    records = []
    for p in pairs:
        png = crop_path(cache, p["crop_ref"])
        exists = png.exists()
        # imread는 손상/권한 문제에서 예외 없이 None을 준다 → crop_ink_ratio(None)이
        # AttributeError로 샌다. 잉크를 재기 전에 crop_status로 가른다(spec §8).
        img = imread(str(png)) if exists else None
        status = crop_status(exists=exists, readable=img is not None)
        ratio = crop_ink_ratio(img) if status == STATUS_OK else None
        records.append({**p, "crop_status": status, "ratio": ratio})
    return records


def _load_manifest(path: Path | None, known_refs: set[str]) -> dict[str, str]:
    """labels.csv를 읽어 검증한다 — 순수 계층의 ValueError를 종료 코드로 바꾼다.

    Raises:
        SystemExit: manifest의 헤더·crop_ref·label 값이 캐시와 맞지 않을 때. 조용히 빠진
            표본은 마진을 낙관적으로 만든다(spec §7). 경로를 잘못 친 경우(OSError)도 같다 —
            이 도구의 다른 경계(resolve_cache·load_cache_meta)는 전부 지시문을 주는데
            여기서만 맨 FileNotFoundError 트레이스백으로 새면 원인을 짚기 어렵다.
    """
    if path is None:
        return {}
    try:
        return load_labels(read_labels_csv(path), known_refs)
    except (OSError, ValueError) as e:
        raise SystemExit(f"labels.csv를 쓸 수 없다({path}): {e}") from e


def _run_report(args: argparse.Namespace, meta: dict) -> None:
    """캐시를 평가해 리포트를 렌더한다(DB 쓰기 없음)."""
    from handwriting.blank_crop import BLANK_INK_MAX

    records = evaluate_cached(args.cache)
    labels = _load_manifest(args.labels, {r["crop_ref"] for r in records})
    md = render_blank_report(records, labels, meta, threshold=BLANK_INK_MAX)
    out = args.cache / "blank_crop_report.md"
    # 리포트 전문이 한국어다 — 인코딩을 고정하지 않으면 플랫폼 로케일을 따라가
    # LC_ALL=C(launchd·CI·cron)에서 UnicodeEncodeError로 죽는다(curation_report 관례).
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"저장: {out}")


def require_same_target(meta: dict, host: str, backend_env: str) -> None:
    """캐시를 만든 쓰기 대상과 지금 쓰려는 대상이 같은지 대조한다(H2).

    조건부 UPDATE의 `WHERE ... status = <seen>`에 실리는 seen 상태는 '**fetch한 DB에서**
    본 상태'다. 스키마·id 채번이 같은 스테이징/운영 사이에서는 그 상태가 우연히 일치할 수
    있어, 대조 없이 쏘면 다른 DB에서 본 근거로 운영 행을 뒤집는 일이 가능하다.

    대상을 정하는 축은 **둘**이다 — ssh 호스트와, 실제 DB 접속값(DB_HOST/DB_NAME)을 담은
    원격 backend env 파일(`SJMJ_REMOTE_BACKEND_ENV`)이다. 호스트만 대조하면 fetch와 apply
    사이에 env를 다른 DB로 돌려놓는 것만으로 이 가드를 통과한다(같은 macmini의 운영 DB와
    테스트 DB가 그 예다). 그래서 fetch가 남긴 env 경로를 함께 대조한다. 같은 경로의 **내용**이
    바뀐 경우까지는 잡지 못한다 — 그건 fetch 시점에 원격 DB 신원을 따로 질의해야 알 수 있다.

    Raises:
        SystemExit: meta의 호스트·backend env가 대상과 다를 때. backend env 키가 아예 없는
            옛 캐시도 같다 — 어느 DB에서 본 상태인지 근거가 없으므로 통과시키지 않는다.
    """
    cached_host = meta.get("host")
    if cached_host != host:
        raise SystemExit(
            f"캐시를 만든 호스트({cached_host})와 쓰기 대상({host})이 다르다 — 조건부 "
            "UPDATE의 seen 상태는 fetch한 DB에서 본 값이라 그대로 쏘면 다른 DB에서 본 "
            f"근거로 운영 행을 뒤집는다. `--host {host}`로 fetch를 다시 실행할 것."
        )
    cached_env = meta.get(META_BACKEND_ENV)
    if cached_env != backend_env:
        missing = "이 캐시엔 backend-env 기록이 없다(fetch가 남기기 전에 만든 캐시)"
        differs = f"캐시를 만든 backend-env({cached_env})와 쓰기 대상({backend_env})이 다르다"
        raise SystemExit(
            f"{missing if cached_env is None else differs} — backend env가 실제 DB"
            "(DB_HOST/DB_NAME)를 정하므로 그대로 쏘면 다른 DB에서 본 근거로 운영 행을 "
            f"뒤집는다. `{ENV_BACKEND_ENV.name}`를 확인하고 fetch를 다시 실행할 것."
        )


def recheck_extras(plan: ApplyPlan) -> dict[str, int]:
    """`--recheck-reviewed`가 추가로 끌어들인 잡·쌍·변경 예정 건수(기본 실행이면 전부 0).

    이 플래그는 파괴 범위를 넓히는데, 무엇이 몇 건 늘었는지 사전에 볼 방법이 없으면
    운영자가 범위를 눈으로 확인할 수 없다.
    """
    pairs = [r for r in plan.targets if r["curation_reviewed"]]
    jobs = {r["job_id"] for r in pairs}
    return {
        "jobs": len(jobs),
        "pairs": len(pairs),
        "updates": sum(1 for u in plan.updates if u.job_id in jobs),
    }


def plan_summary_lines(plan: ApplyPlan, *, host: str, backend_env: str) -> list[str]:
    """쓰기 **전에** 보여줄 계획 요약 — 대상/보호/불변/변경 예정과 쓰기 대상 자체를 싣는다."""
    extras = recheck_extras(plan)
    lines = [
        f"쓰기 대상: {host} · backend-env {backend_env}",
        f"대상 {len(plan.targets)} · 보호 {plan.counts['protected']} · "
        f"불변 {plan.counts['unchanged']} · 변경 예정 {len(plan.updates)} · "
        f"보류 {len(plan.holds)}",
    ]
    if extras["pairs"]:
        lines.append(
            f"--recheck-reviewed로 추가된 잡 {extras['jobs']} · 쌍 {extras['pairs']} · "
            f"변경 예정 {extras['updates']}"
        )
    return lines


def _run_apply(args: argparse.Namespace, meta: dict) -> None:
    """판정을 운영 DB에 반영한다(쓰기 — report를 눈으로 본 뒤에만 친다).

    Args:
        args: 파싱된 CLI 인자.
        meta: 캐시 meta.json — 이 캐시를 만든 대상(호스트·backend env)을 쓰기 대상과
            대조한다(H2).

    Raises:
        RuntimeError: 임계 미확정(캐시 평가·원격 접근보다 먼저 멈춘다, spec §8).
        RemoteError: 원격 mysql이 비-0으로 끝났을 때. stdout의 ROW_COUNT 프로브는 COMMIT
            앞에서 찍히므로 출력만으로 성공을 판정하면 거짓 안심이 된다(M4) — 성공 판정은
            run_ssh의 비-0 종료 검사를 통과한 뒤에만 한다.
        SystemExit: 캐시와 쓰기 대상(호스트·backend env)이 다를 때 · 보류·충돌·계획 밖
            id가 남았을 때
            (비-0 종료, spec §8).
    """
    from handwriting.blank_crop import require_blank_ink_max

    threshold = require_blank_ink_max()
    backend_env = env_or(ENV_BACKEND_ENV)
    require_same_target(meta, args.host, backend_env)
    plan = plan_apply(
        evaluate_cached(args.cache), threshold, recheck_reviewed=args.recheck_reviewed
    )
    # 요약은 쓰기 **앞**에 찍는다 — 뒤에 있으면 원격이 죽었을 때 무엇을 쏘려 했는지도 남지 않는다.
    for line in plan_summary_lines(plan, host=args.host, backend_env=backend_env):
        print(line)
    if args.dry_run:
        print("--dry-run — 원격에 아무것도 쏘지 않고 끝낸다.")
        return

    result: dict[str, list[int]] = {"changed": [], "conflict": [], "unknown": []}
    if plan.updates:
        script = mysql_script(backend_env, build_apply_script(plan.updates))
        result = classify_affected(
            plan.updates, parse_apply_output(run_ssh(args.host, script).decode())
        )

    print(f"변경 {len(result['changed'])} · 충돌 {len(result['conflict'])}")
    if result["conflict"]:
        print(
            f"⚠️  충돌(fetch 이후 사람이 PATCH함) — 재-fetch 후 다시 실행할 것: {result['conflict']}"
        )
    if result["unknown"]:
        print(f"⚠️  계획에 없는 pair_id가 출력에 섞였다(stale/잘린 출력): {result['unknown']}")
    for r in plan.holds:
        print(f"⚠️  보류 {r['crop_status']}: {r['crop_ref']} (DB 미변경)")

    code = apply_exit_code(
        plan.holds,
        result["conflict"],
        allow_holds=args.allow_holds,
        unknown=result["unknown"],
    )
    if code:
        raise SystemExit(
            f"보류 {len(plan.holds)}건 · 충돌 {len(result['conflict'])}건 · "
            f"계획 밖 {len(result['unknown'])}건 — 보류는 크롭 재생성 또는 사람 배제로 해소"
            "(무시하려면 --allow-holds), 충돌·계획 밖은 재-fetch 후 다시 실행할 것"
        )


def resolve_cache(cache: Path) -> Path:
    """--cache를 절대경로로 굳히고 남의 디렉터리를 캐시로 지목하는 자해를 막는다.

    무검증이면 `--cache ""`는 cwd, `--cache /`는 `/`가 되고 fetch가 그 안의 `crops/`를
    통째로 rmtree 대상으로 삼는다. 비었거나 없는 디렉터리는 첫 fetch이므로 통과시키고,
    내용이 있는데 이 도구의 산출이 하나도 없으면 거부한다(중단된 fetch로 매니페스트만
    없는 상태는 `crops/`가 남아 있어 계속 통과한다).

    Raises:
        SystemExit: 이 도구의 캐시로 보이지 않는 비어 있지 않은 디렉터리일 때.
    """
    resolved = cache.resolve()
    if not resolved.is_dir():
        return resolved
    owned = (PAIRS_NAME, "meta.json", "crops", "blank_crop_report.md")
    if any(resolved.iterdir()) and not any((resolved / name).exists() for name in owned):
        raise SystemExit(
            f"--cache가 이 도구의 캐시가 아니다({resolved}) — fetch는 그 안의 crops/를 "
            "통째로 지운다. 빈 디렉터리나 기존 캐시를 지정할 것."
        )
    return resolved


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report/apply)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="blank_crop_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="training_pairs + 품목 크롭 PNG 동기화")
    p_rep = sub.add_parser("report", help="캐시 평가 → blank_crop_report.md")
    p_rep.add_argument("--labels", type=Path, help="육안 라벨 manifest(labels.csv)")
    p_app = sub.add_parser("apply", help="판정을 운영 DB에 반영(쓰기)")
    p_app.add_argument("--recheck-reviewed", action="store_true", help="검수 완료 잡까지 포함")
    p_app.add_argument("--allow-holds", action="store_true", help="보류가 있어도 0으로 종료")
    p_app.add_argument("--dry-run", action="store_true", help="계획만 출력하고 쓰지 않는다")
    args = ap.parse_args(argv)
    args.cache = resolve_cache(args.cache)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, env_or(ENV_BACKEND_ENV), env_or(ENV_WORKER_ENV), args.cache)
        print(f"동기화 완료 → {args.cache} (쌍 {meta['n_pairs']} · 크롭 {meta['n_crops']})")
        return

    meta = load_cache_meta(args.cache, PAIRS_NAME, tool="blank_crop_report")
    if args.cmd == "report":
        _run_report(args, meta)
        return
    _run_apply(args, meta)


if __name__ == "__main__":
    main()

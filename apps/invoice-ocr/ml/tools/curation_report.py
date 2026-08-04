"""OCR 큐레이션 학습쌍(training_pairs) 정확도 분석 리포트 도구.

배포 서버(macmini)의 운영 DB·모델뱅크·크롭 이미지를 ssh로 동기화해 로컬 캐시에 두고,
품목 retrieval(top1/top5·뱅크 내외 분해)과 금액 OCR(0-드리프트·퇴화출력·오독)의 실패를
버킷으로 귀속한 마크다운 리포트를 만든다. LLM 에이전트가 리포트→실패 크롭 시각 검수→
개선(뱅크 추가·warp 재검토) 루프를 돌리기 위한 입구다. 사용법은 docs/runbooks 참조.

코어 규약 준수: stdlib 전용(paddle/torch 불필요), 순수 계층은 세 모듈에 분리돼 있고 의존은
단방향이다 — 이 모듈(fetch 글루·CLI) → tools/curation_render.py(렌더) →
tools/curation_enrich.py(파싱·버킷·조인·집계) → tools/curation_cohort.py(코호트·평가 가능성
술어·재평가 게이트). ssh/DB 접근은 fetch 글루에 격리. 원격 접속값은 env로만 주입한다.

Usage:
    uv run python -m tools.curation_report fetch        # 서버에서 pairs/jobs/교정 이력/bank/재평가 동기화
    uv run python -m tools.curation_report report       # 캐시 분석 → report.md/failures.jsonl
    uv run python -m tools.curation_report pull-images  # 실패 잡 크롭(+원본) 로컬 동기화
"""

import argparse
import io
import json
import os
import shlex
import tarfile
from datetime import UTC, datetime
from pathlib import Path

# bank_id는 stdlib 전용이고 handwriting에 __init__.py가 없는 암묵 namespace 패키지라
# 모듈 레벨 import가 numpy/torch를 끌지 않는다(paddle-free 코어 규약 유지).
from handwriting.bank_id import file_digest
from tools.curation_cohort import (
    is_item_failure,
    parse_reeval_jsonl,
    reeval_after,
    reeval_gate,
)
from tools.curation_enrich import (
    CORRECTIONS_SQL,
    JOBS_SQL,
    PAIRS_SQL,
    enrich_pairs,
    parse_corrections_tsv,
    parse_jobs_tsv,
    parse_pairs_tsv,
)
from tools.curation_render import NO_FINGERPRINT_NOTICE, render_report
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_ML_ROOT,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    RemoteError,
    env_or,
    mysql_script,
    remote_path,
    run_ssh,
    source_env,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "curation"


# ---------------------------------------------------------------------------
# ssh fetch 글루 (원격 접속 — 단위테스트 비대상)
# ---------------------------------------------------------------------------


# 원격 인라인 스크립트에 지문 로직도 그 **입력**(모델·뱅크 파일명·배열 선택)도 복붙하지 않는다 —
# bank_id.bank_retrieval_version 하나를 워커와 공유한다(M4). 두 곳이 다른 입력을 고르면 지문이
# 전량 어긋나 모든 잡이 조용히 stale이 된다(spec §3-A). 계산은 **원격에서** 해야 유효하다:
# 코드 SHA가 입력이라 로컬 계산은 전 잡을 조용히 stale로 오분류한다. 지문 계산만 try로 감싼다
# (M3) — keys 없는 뱅크는 실재 가능하고(운영 워커도 진단 필드 하나로 격리한다) 그 실패로
# pairs/jobs 동기화까지 막을 이유가 없다. `handwriting` import는 try 밖이라 hard-fail을 유지한다
# (배포 누락 신호). 사유는 **stdout 페이로드**에 싣는다 — stderr는 종료코드 0인 이 경로에서
# run_ssh가 통째로 버려 원인(git SHA 부재/npz 결손/모델 접근 실패)을 구분할 창구가 로컬에
# 남지 않는다(`result_json` 스탬프 규칙과 다른 축 — 이건 fetch 캐시의 진단 필드다).
# 셸 이중따옴표 안에 그대로 들어가므로 `"`·`$`·백틱·백슬래시를 쓰지 않는다.
_BANK_PY = """
import collections, json, os
import numpy as np
from handwriting import bank_id
d = os.environ['SJMJ_ML_MODELS_DIR']
z = np.load(os.path.join(d, bank_id.BANK_FILENAME), allow_pickle=True)
labs = [str(x) for x in z['lab']]
try:
    version = bank_id.bank_retrieval_version(d, z, labs)
    error = None
except Exception as e:
    error = '%s: %s' % (type(e).__name__, e)
    version = None
print(json.dumps({'size': len(labs), 'counts': collections.Counter(labs),
                  'retrieval_version': version, 'retrieval_version_error': error},
                 ensure_ascii=False))
"""

# `from handwriting import bank_id`가 서버에서 낼 수 있는 두 문구 — 이 문구가 곧 지문 기능(#49)
# 이전 릴리스 신호다(다른 모듈 부재는 다른 원인이다). 배포 서버에는 `handwriting/`이 이미 있고
# `bank_id.py`만 없어 CPython이 ModuleNotFoundError가 아닌 ImportError를 내며(실측), 그쪽이 주
# 경로다 — "No module named"만 보면 정작 주 시나리오에서 안내문이 발화하지 않는다.
_FINGERPRINT_IMPORT_MARKERS = (
    "No module named 'handwriting'",  # handwriting/ 자체가 없다
    "cannot import name 'bank_id' from 'handwriting'",  # bank_id.py만 없다
)
# 상단 메시지에 실을 원격 stderr 꼬리 줄 수 — traceback 전문은 길고 원인은 끝에 있다.
_STDERR_EXCERPT_LINES = 3

# 캐시 손상은 원인이 무엇이든 복구 절차가 하나다 — 서버에서 다시 받는다.
_CACHE_RECOVERY = "로컬 캐시 손상이다. `fetch`를 다시 실행한다."

# 재평가 산출물이 사는 원격 하위 경로(bank_update.DEFAULT_OUT과 같은 자리).
REEVAL_SUBDIR = "results/bank_update"
# (원격 파일명, 캐시 파일명). 순서가 곧 쓰기 순서다 — jsonl 먼저, 그 다음 meta.
REEVAL_FILES = (("score.jsonl", "reeval.jsonl"), ("score_meta.json", "reeval_meta.json"))
# 원자 교체 한 벌의 마지막 파일 — 앞의 두 파일을 **해석하는** 쪽이라 가장 나중에 갈아끼운다(M2).
CACHE_META = "meta.json"


def bank_script(worker_env: str, ml_root: str) -> str:
    """원격 뱅크 라벨 집계 + 현재 retrieval 지문을 한 번에 얻는 셸 스크립트를 만든다.

    ml_root로 cd하는 이유: `python -c`는 cwd를 sys.path에 넣으므로 그래야 handwriting
    패키지를 import할 수 있다. 서버 레포에 handwriting.bank_id가 없으면(릴리스 배포 전)
    ModuleNotFoundError로 크게 실패한다 — 지문 없이 조용히 진행하면 전 표본이 stale/unknown
    으로 떨어져 품목 지표가 0/0이 되므로, 원인을 메시지로 풀어 주는 편이 낫다.
    """
    return f'{source_env(worker_env)}cd "{remote_path(ml_root)}"; "$PYTHON_BIN" -c "{_BANK_PY}"'


def reeval_probe_script(ml_root: str) -> str:
    """재평가 산출물 존재를 확인한다 — 부재는 정상 상태이므로 비0으로 죽지 않는다."""
    return (
        f'cd "{remote_path(ml_root)}/{REEVAL_SUBDIR}" 2>/dev/null || exit 0; '
        "ls score.jsonl score_meta.json 2>/dev/null || true"
    )


def reeval_cat_script(ml_root: str, name: str) -> str:
    """재평가 산출물 1개를 그대로 읽어온다(name은 REEVAL_FILES의 상수다)."""
    return f'cat "{remote_path(ml_root)}/{REEVAL_SUBDIR}/{name}"'


def _replace_atomically(cache: Path, files: list[tuple[str, bytes]]) -> None:
    """항상 함께 움직여야 하는 파일들을 **전부** tmp로 받은 뒤 순서대로 교체한다.

    한 벌은 셋이다(M2): 재평가 두 파일과 **그 둘을 해석하는** meta.json(retrieval_version·
    reeval_state). 앞의 둘만 원자적이면 meta.json이 평범한 쓰기로 먼저 굳어, 짝이 어긋난 상태의
    사유가 stale로 오보된다 — 수치는 fail-closed라 안전하지만 사용자는 잘못된 조치로 간다.
    교체 사이에 죽는 창은 남으므로 순서를 고정한다(호출자가 준 순서 = REEVAL_FILES + meta.json):
    meta.json 교체 전에 죽으면 이전 meta의 지문·다이제스트가 새 산출물과 어긋나 게이트가
    "재평가 없음"으로 닫는다(fail-closed).
    """
    staged = [(cache / name, cache / f"{name}.tmp", body) for name, body in files]
    try:
        for _path, tmp, body in staged:
            tmp.write_bytes(body)
        for path, tmp, _body in staged:
            os.replace(tmp, path)
    except Exception:
        for _path, tmp, _body in staged:
            tmp.unlink(missing_ok=True)
        raise


def _clear_reeval(cache: Path) -> None:
    """캐시의 재평가 두 파일을 함께 지운다 — 두 파일은 항상 같이 움직인다.

    남겨두면 서버에서 산출물이 사라지거나 옮겨진 뒤에도 로컬 reeval_meta.json이 살아남아 재평가가
    유효한 것처럼 읽힌다(warp_gate_report.fetch_all이 이전 산출을 먼저 rmtree하는 것과 같은 이유).
    """
    for _remote, local in REEVAL_FILES:
        (cache / local).unlink(missing_ok=True)


def _read_reeval_files(jsonl_path: Path, meta_path: Path) -> tuple[list[dict], dict]:
    """캐시의 재평가 두 파일을 읽는다 — 손상은 파일명·복구 지침과 함께 경계에서 막는다(H2).

    `parse_reeval_jsonl`이 dict 아닌 줄을 막는 것과 같은 이유로 meta도 dict 여부를 본다: 게이트
    안쪽까지 흘러가면 dict가 아닌 값에 AttributeError가 나 원인이 파싱 경계에서 멀어진다(`null`은
    게이트가 no_meta로 정상 처리하는데 `_reeval_info`가 먼저 죽었다). 읽기 인코딩도 여기서
    못박는다(L5) — 쓰기는 UTF-8 bytes다.

    Raises:
        json.JSONDecodeError: score.jsonl이 파싱되지 않을 때(즉시 실패 계약 유지 — 이 타입은
            ValueError의 하위형이라 호출자는 ValueError 하나로 잡는다).
        ValueError: score_meta.json이 파싱되지 않거나 JSON 객체가 아닐 때.
    """
    try:
        records = parse_reeval_jsonl(jsonl_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"{jsonl_path.name} 손상({e.msg}) — {_CACHE_RECOVERY}", e.doc, e.pos
        ) from e
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{meta_path.name} 손상({e.msg}) — {_CACHE_RECOVERY}") from e
    if not isinstance(meta, dict):
        raise ValueError(
            f"{meta_path.name}이 JSON 객체가 아니다({type(meta).__name__}) — {_CACHE_RECOVERY}"
        )
    return records, meta


def _reeval_info(cache: Path, meta: dict) -> tuple[dict | None, dict]:
    """캐시의 재평가를 유효성 게이트에 통과시키고 리포트용 상태 정보를 만든다.

    `after`는 `reeval_after`로 평탄화한다 — score_meta는 지문을 중첩(`{before, after}`)으로
    쓰는데 `reeval_notice`는 평탄 키를 읽으므로, 재맵을 빠뜨리면 채택 문구가 지문을 못 찾는다.

    Returns:
        (게이트를 통과한 {crop_ref: 레코드} 또는 None, meta["reeval"]에 실을 상태 정보).
    """
    info = {
        "state": meta.get("reeval_state", "absent"),
        "adopted": False,
        "reason": None,
        "generated_at": None,
        "after": None,
        "scope": None,
        "n_pairs": None,
    }
    jsonl_path, meta_path = cache / "reeval.jsonl", cache / "reeval_meta.json"
    if info["state"] != "present" or not (jsonl_path.exists() and meta_path.exists()):
        return None, info
    records, reeval_meta = _read_reeval_files(jsonl_path, meta_path)
    gate = reeval_gate(
        records=records,
        meta=reeval_meta,
        current_retrieval_version=meta.get("retrieval_version"),
        jsonl_sha256=file_digest(jsonl_path),
    )
    return gate.pairs, {
        **info,
        "adopted": gate.pairs is not None,
        "reason": gate.reason,
        "generated_at": reeval_meta.get("generated_at"),
        "after": reeval_after(reeval_meta),
        "scope": reeval_meta.get("scope"),
        "n_pairs": reeval_meta.get("n_pairs"),
    }


def fetch_error_message(stderr: str) -> str | None:
    """서버가 지문 기능(Issue #49) 이전 릴리스일 때 쓸 행동 지침을 만든다. 아니면 None.

    배포 전에는 서버 레포에 handwriting.bank_id가 없다 — hard-fail은 의도이지만 raw
    traceback은 행동 지침이 아니다. 문자열 판정만 하는 순수 헬퍼라 ssh 없이 단위테스트로 닫는다.

    판정은 **모듈명까지** 본다(M1). `No module named` 단독 매칭은 서버 venv의 numpy/torch
    부재까지 "#49 이전 릴리스"로 오진하는데, 그 경우 배포는 이미 됐고 원인은 venv라 지침이
    엉뚱하다. 대신 문구는 예외 2종을 모두 본다(`_FINGERPRINT_IMPORT_MARKERS` 참조 — 주 경로가
    ImportError다). 원본 stderr 발췌도 싣는다 — 삼키면 어떤 모듈이 없는지 볼 창구가 사라진다.

    raise가 아니라 메시지를 반환한다 — 조건부로만 던지는 헬퍼는 호출부에서 제어흐름이 보이지
    않아, 반환 후 다음 줄이 실행되는지를 헬퍼 본문을 열어야 알 수 있다.
    """
    if not any(marker in stderr for marker in _FINGERPRINT_IMPORT_MARKERS):
        return None
    excerpt = " / ".join(stderr.strip().splitlines()[-_STDERR_EXCERPT_LINES:])
    return (
        "서버 코드가 retrieval 지문 기능(Issue #49) 이전 릴리스다 — "
        "`v*` 태그 배포 후 다시 실행한다. "
        "(배포 전에는 기존 캐시로 `report`를 돌려 금액·excluded 검수 루프를 계속할 수 있다.) "
        f"원격 stderr: {excerpt}"
    )


def _fetch_reeval(host: str, ml_root: str, cache: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """서버의 재평가 산출물을 회수해 (회수 상태 ReevalState, 캐시에 쓸 (파일명, 내용))을 낸다.

    쓰기는 호출자가 meta.json과 **한 벌로** 교체한다(M2) — 그래야 세 파일이 함께 움직인다.
    부재 경로에서는 이전 회수분을 즉시 지운다(그 자리에 쓸 새 내용이 없으므로 한 벌에 넣을 수
    없고, 남겨두면 재평가가 유효한 것처럼 읽힌다).
    """
    names = set(run_ssh(host, reeval_probe_script(ml_root)).decode().split())
    if {remote for remote, _local in REEVAL_FILES} <= names:
        bodies = [
            (local, run_ssh(host, reeval_cat_script(ml_root, remote)))
            for remote, local in REEVAL_FILES
        ]
        return "present", bodies
    _clear_reeval(cache)
    # score.jsonl만 있는 상태는 정상 경로다(#53 이전 산출물) — 리포트가 한 줄 알린다.
    return ("no_meta" if "score.jsonl" in names else "absent"), []


def _write_json(path: Path, obj) -> None:
    """캐시 JSON 1개를 UTF-8로 쓴다 — 읽기(`_load_enriched`)와 인코딩을 맞춘다(L5)."""
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_all(*, host: str, backend_env: str, worker_env: str, ml_root: str, cache: Path) -> dict:
    """서버에서 training_pairs·result_json·교정 이력·뱅크 라벨·현재 지문·재평가 산출물을 동기화한다.

    인자는 키워드 전용이다 — 동종 str 4개(host·두 env 경로·ml_root)가 인접해 위치로 넘기면
    뒤바꿔도 예외가 안 나고, ml_root가 뒤에 끼어든 시점부터 조용한 오연결 위험이 커졌다.
    """
    cache.mkdir(parents=True, exist_ok=True)
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL, raw=False)).decode())
    jobs = parse_jobs_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    corrections = parse_corrections_tsv(
        run_ssh(host, mysql_script(backend_env, CORRECTIONS_SQL, raw=False)).decode()
    )
    try:
        bank = json.loads(run_ssh(host, bank_script(worker_env, ml_root)).decode())
    except RemoteError as e:
        message = fetch_error_message(str(e))
        if message:
            raise RuntimeError(message) from e
        raise

    reeval_state, reeval_files = _fetch_reeval(host, ml_root, cache)
    meta = {
        "fetched_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "host": host,
        "bank_size": bank["size"],
        "bank_distinct": len(bank["counts"]),
        "retrieval_version": bank.get("retrieval_version"),
        "retrieval_version_error": bank.get("retrieval_version_error"),  # 원격 진단(성공 시 None)
        "reeval_state": reeval_state,
    }
    _write_json(cache / "pairs.json", pairs)
    _write_json(cache / "jobs.json", jobs)
    _write_json(cache / "bank.json", bank)
    # 네 번째 소스. 재평가 3파일의 원자 교체 한 벌에는 넣지 않는다 — 그 한 벌은 지문 해석
    # 짝이 어긋나지 않게 하는 별개 축이다(spec §3-2). "네 번째"는 spec §2의 논리 소스 축
    # 표기다(재평가 2파일이 1행). 런북 표는 후속 태스크에서 파일 기준 "다섯"으로 갱신된다
    # (Refs #72) — 이 커밋 시점에는 아직 "넷"이다.
    _write_json(cache / "corrections.json", corrections)
    # 재평가 두 파일과 그 해석자(meta.json)는 한 벌로 갈아끼운다 — 순서는 해석자가 마지막.
    meta_body = json.dumps(meta, ensure_ascii=False, indent=1).encode()
    _replace_atomically(cache, [*reeval_files, (CACHE_META, meta_body)])
    return meta


def pull_images(
    host: str, backend_env: str, cache: Path, job_ids: list[int], with_originals: bool
) -> Path:
    """지정 잡들의 크롭 디렉터리(+옵션 원본 사진)를 캐시로 동기화한다."""
    out_dir = cache / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 빈 목록이면 원격 tar가 인자 없이 실행돼 exit 1로 죽는다 — 정상 상태이므로 no-op.
    if not job_ids:
        return out_dir
    names = " ".join(f"job-{j}" for j in job_ids)
    tar_script = f'{source_env(backend_env)}tar -C "$SJMJ_DATA_DIR/ocr_crops" -cf - {names}'
    with tarfile.open(fileobj=io.BytesIO(run_ssh(host, tar_script))) as tf:
        tf.extractall(out_dir, filter="data")
    if with_originals:
        jobs = json.loads((cache / "jobs.json").read_text())
        for j in jobs:
            if j["job_id"] in job_ids:
                # image_path는 신뢰 DB 값이지만 원격 셸에 들어가므로 방어적으로 quote한다.
                data = run_ssh(host, f"cat {shlex.quote(j['image_path'])}")
                dst = out_dir / f"job-{j['job_id']}"
                dst.mkdir(parents=True, exist_ok=True)
                (dst / "original.jpg").write_bytes(data)
    return out_dir


def _write_images_index(cache: Path, enriched: list[dict], job_ids: list[int]) -> Path:
    """가져온 크롭을 검수할 때 참조할 ref→파일→라벨 인덱스를 만든다.

    M6: 판정 술어(`is_item_evaluable`/`is_item_failure`)로 거르지 않고 그 잡의 행 전량을
    나열한다(의도) — 이 함수는 spec §3-C 소비자 표에 없는 **표시용**이다. `pull-images`로 당겨온
    잡은 검수자가 크롭을 육안으로 보며 판정하므로, 판정 불가 행도 같이 보여야 "이 행이 왜 판정
    불가인지"를 그 자리에서 확인할 수 있다.
    """
    lines = ["# 큐레이션 크롭 검수 인덱스", ""]
    for r in enriched:
        if r["job_id"] not in job_ids:
            continue
        lines.append(
            f"- images/{r['crop_ref']}.png · answer={r['answer']!r} (final={r['final_label']!r}) "
            f"draft={r['draft_label']!r} [{r['label_bucket']}/{r['amount_bucket']}] "
            f"supply={r['supply']} raw={r['amount_raw']!r}"
        )
    path = cache / "images_index.md"
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_enriched(cache: Path) -> tuple[list[dict], dict]:
    """캐시를 읽어 재평가·현재 지문까지 배선한 enriched 행과 meta를 만든다.

    `reeval`·`current_retrieval_version`을 넘기지 않으면 코호트 판정이 기준값을 잃어 리포트가
    전량 `unevaluable`로 떨어진다 — 이 배선이 곧 era-aware 재판정의 소비 지점이다.
    """
    pairs = json.loads((cache / "pairs.json").read_text(encoding="utf-8"))
    jobs = json.loads((cache / "jobs.json").read_text(encoding="utf-8"))
    bank = json.loads((cache / "bank.json").read_text(encoding="utf-8"))
    meta = json.loads((cache / CACHE_META).read_text(encoding="utf-8"))
    reeval, info = _reeval_info(cache, meta)
    enriched = enrich_pairs(
        pairs,
        jobs,
        set(bank["counts"]),
        reeval=reeval,
        current_retrieval_version=meta.get("retrieval_version"),
    )
    return enriched, {**meta, "reeval": info}


def _require_exclusion_reason(enriched: list[dict]) -> None:
    """배제 집계를 소비하기 직전에 구버전 pairs.json 캐시를 막는다.

    exclusion_reason 컬럼 신설 이전 fetch가 만든 pairs.json은 이 키가 없다. 조용히 통과시키면
    사람/기계 배제·되돌림 집계가 모두 0으로 보여 오탐률(ADR 0006)을 숨기게 되므로 즉시 실패시켜
    재동기화를 유도한다.

    검사를 `_load_enriched`가 아니라 이 소비자 앞에 둔다 — `pull-images`는 status만 읽고
    exclusion_reason을 한 번도 보지 않는데, 공통 경로에서 막으면 크롭 검수까지 함께 죽는다.
    하필 그 상황이 `fetch_error_message`가 "배포 전에는 기존 캐시로 검수 루프를 계속하라"고
    안내하는 바로 그 상황이다.

    Raises:
        ValueError: pairs.json이 exclusion_reason 키 없는 구버전일 때.
    """
    if enriched and "exclusion_reason" not in enriched[0]:
        raise ValueError(
            f"pairs.json 캐시가 구버전이다(exclusion_reason 키 없음) — {_CACHE_RECOVERY}"
        )


def _failure_job_ids(enriched: list[dict]) -> list[int]:
    """pull-images 기본 대상 — 검수 대상 실패가 있는 잡 + excluded가 있는 잡.

    판정 불가만 있는 잡은 당기지 않는다(전 잡 폭주 방지). 재평가 전에는 금액 실패·excluded
    기반 검수 루프만 돌고, 품목 크롭 검수는 재평가 이후에 의미가 생긴다(spec §5).
    """
    return sorted(
        {r["job_id"] for r in enriched if r["status"] == "excluded" or is_item_failure(r)}
    )


def _cmd_fetch(host: str, backend_env: str, worker_env: str, ml_root: str, cache: Path) -> None:
    """fetch 서브커맨드 — 동기화하고 다음 조치를 판단할 요약을 출력한다.

    지문이 미확정이면 그 사실·사유·조치를 그 자리에서 말한다(M3·H1) — 원격 지문 계산 실패는
    fetch를 죽이지 않고 null로 통과시키므로, 여기서 안 알리면 리포트가 전량 stale_bank로 나온
    뒤에야 원인을 찾게 된다. 사유는 원격 진단을 그대로 옮기고, 없으면 "없다"고 단정하지 않는다.
    """
    meta = fetch_all(
        host=host, backend_env=backend_env, worker_env=worker_env, ml_root=ml_root, cache=cache
    )
    version = meta["retrieval_version"]
    print(f"동기화 완료 → {cache} ({meta['fetched_at']})")
    print(f"현재 retrieval 지문: {version or '미확정'} · 재평가: {meta['reeval_state']}")
    if not version:
        print(f"지문 계산 실패 사유: {meta['retrieval_version_error'] or '미상(원격 진단 없음)'}")
        print(NO_FINGERPRINT_NOTICE)


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report/pull-images)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="curation_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="서버에서 pairs/jobs/교정 이력/bank/재평가 동기화")
    sub.add_parser("report", help="캐시 분석 → report.md + failures.jsonl")
    p_img = sub.add_parser("pull-images", help="실패 잡 크롭 동기화(기본: 실패 잡 전체)")
    p_img.add_argument("--jobs", type=int, nargs="*", help="특정 잡만")
    p_img.add_argument("--originals", action="store_true", help="원본 사진도 포함")
    args = ap.parse_args(argv)

    backend_env = env_or(ENV_BACKEND_ENV)
    worker_env = env_or(ENV_WORKER_ENV)
    ml_root = env_or(ENV_ML_ROOT)

    if args.cmd == "fetch":
        _cmd_fetch(args.host, backend_env, worker_env, ml_root, args.cache)
        return

    enriched, meta = _load_enriched(args.cache)

    if args.cmd == "report":
        _require_exclusion_reason(enriched)
        report = render_report(enriched, meta)
        report_path = args.cache / "report.md"
        report_path.write_text(report)
        # 에이전트가 소비하는 실패 목록 — unevaluable이 섞이면 이슈가 지적한 왜곡이
        # 산출물에 그대로 남는다(spec §3-C).
        failures = [r for r in enriched if is_item_failure(r)]
        fail_path = args.cache / "failures.jsonl"
        fail_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in failures) + "\n"
        )
        print(report)
        print(f"저장: {report_path}\n저장: {fail_path}")
        return

    if args.cmd == "pull-images":
        job_ids = args.jobs or _failure_job_ids(enriched)
        if not job_ids:
            print("실패 잡이 없어 가져올 이미지가 없습니다.")
            return
        out_dir = pull_images(args.host, backend_env, args.cache, job_ids, args.originals)
        index = _write_images_index(args.cache, enriched, job_ids)
        print(f"이미지 동기화 → {out_dir} (잡 {len(job_ids)}개)\n인덱스: {index}")


if __name__ == "__main__":
    main()

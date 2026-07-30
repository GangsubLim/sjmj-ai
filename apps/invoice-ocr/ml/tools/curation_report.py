"""OCR 큐레이션 학습쌍(training_pairs) 정확도 분석 리포트 도구.

배포 서버(macmini)의 운영 DB·모델뱅크·크롭 이미지를 ssh로 동기화해 로컬 캐시에 두고,
품목 retrieval(top1/top5·뱅크 내외 분해)과 금액 OCR(0-드리프트·퇴화출력·오독)의 실패를
버킷으로 귀속한 마크다운 리포트를 만든다. LLM 에이전트가 리포트→실패 크롭 시각 검수→
개선(뱅크 추가·warp 재검토) 루프를 돌리기 위한 입구다. 사용법은 docs/runbooks 참조.

코어 규약 준수: stdlib 전용(paddle/torch 불필요), 분석 계층은 순수함수(테스트 대상),
ssh/DB 접근은 fetch 글루에 격리. 원격 접속값은 env로만 주입한다. 시점 정합 판정(코호트·
평가 가능성 술어·재평가 유효성 게이트)은 tools/curation_cohort.py에 분리돼 있다.

Usage:
    uv run python -m tools.curation_report fetch        # 서버에서 pairs/jobs/bank 동기화
    uv run python -m tools.curation_report report       # 캐시 분석 → report.md/failures.jsonl
    uv run python -m tools.curation_report pull-images  # 실패 잡 크롭(+원본) 로컬 동기화
"""

import argparse
import io
import json
import shlex
import tarfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from tools.curation_cohort import (
    is_amount_failure,
    is_item_evaluable,
    is_item_failure,
)
from tools.remote import (
    ENV_BACKEND_ENV,
    ENV_SSH_HOST,
    ENV_WORKER_ENV,
    env_or,
    mysql_script,
    run_ssh,
    source_env,
)

ML_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = ML_ROOT / "results" / "curation"

PAIR_COLS = (
    "id, crop_ref, job_id, row_index, draft_label, final_label, "
    "canonical_label, supply, status, reviewed_at"
)
PAIRS_SQL = f"SELECT {PAIR_COLS} FROM training_pairs ORDER BY job_id, row_index"
JOBS_SQL = (
    "SELECT id, image_path, JSON_UNQUOTE(result_json) FROM ocr_jobs "
    "WHERE id IN (SELECT DISTINCT job_id FROM training_pairs)"
)

# ---------------------------------------------------------------------------
# 순수 분석 계층 (단위테스트 대상 — IO 없음)
# ---------------------------------------------------------------------------


def _cell(value: str) -> str | None:
    return None if value == "NULL" else value


def parse_pairs_tsv(text: str) -> list[dict]:
    """mysql --batch TSV(training_pairs)를 타입 변환된 dict 리스트로 파싱한다."""
    lines = text.strip().split("\n")
    header = lines[0].split("\t")
    out = []
    for ln in lines[1:]:
        d = dict(zip(header, ln.split("\t"), strict=True))
        supply = _cell(d["supply"])
        out.append(
            {
                "id": int(d["id"]),
                "crop_ref": d["crop_ref"],
                "job_id": int(d["job_id"]),
                "row_index": int(d["row_index"]),
                "draft_label": _cell(d["draft_label"]),
                "final_label": _cell(d["final_label"]),
                "canonical_label": _cell(d["canonical_label"]),
                "supply": None if supply is None else int(supply),
                "status": d["status"],
                "reviewed_at": _cell(d["reviewed_at"]),
            }
        )
    return out


def parse_jobs_tsv(text: str) -> list[dict]:
    """mysql --batch --raw TSV(ocr_jobs + result_json)를 파싱한다."""
    out = []
    for ln in text.strip().split("\n")[1:]:
        job_id, image_path, raw = ln.split("\t", 2)
        # image_path는 업로드 파일명 suffix를 물려받아 탭이 섞일 수 있다(--raw는 비이스케이프).
        # 컬럼 경계가 밀리면 조용한 오파싱 대신 즉시 실패시킨다.
        if not raw.lstrip().startswith("{"):
            raise ValueError(f"jobs TSV 컬럼 경계 오류(job_id={job_id}) — image_path 제어문자 의심")
        out.append({"job_id": int(job_id), "image_path": image_path, "result": json.loads(raw)})
    return out


def label_bucket(final: str | None, top5_labels: list[str], bank: set[str]) -> str:
    """품목 결과를 실패 원인 버킷으로 귀속한다.

    ok(=top1 적중) / out_of_bank(뱅크에 정답 없음 — 구조적 실패) /
    top5_only(후보엔 있었음) / in_bank_miss(뱅크에 있는데 후보 밖) / no_candidates.
    """
    if not top5_labels:
        return "no_candidates"
    if final == top5_labels[0]:
        return "ok"
    if final not in bank:
        return "out_of_bank"
    if final in top5_labels:
        return "top5_only"
    return "in_bank_miss"


def amount_bucket(draft: int | None, final: int) -> str:
    """금액 결과를 실패 원인 버킷으로 귀속한다.

    degenerate(초안 무산출 draft=None — '!!!' 등 퇴화 출력)· zero_drift(0으로 읽음 —
    warp/칸위치 의심)· sign_mismatch(부호만 상이)· misread(다른 숫자)· ok.
    """
    if draft is None:
        return "degenerate"
    if draft == final:
        return "ok"
    if draft == 0 and final != 0:
        return "zero_drift"
    if draft == -final:
        return "sign_mismatch"
    return "misread"


def enrich_pairs(pairs: list[dict], jobs: list[dict], bank: set[str]) -> list[dict]:
    """training_pairs에 result_json(top5·초안금액)과 뱅크 존재 여부를 조인해 버킷을 매긴다."""
    rows_by_ref = {r.get("crop_ref"): r for j in jobs for r in (j["result"].get("rows") or [])}
    out = []
    for p in pairs:
        row = rows_by_ref.get(p["crop_ref"])
        # 조인 실패(재처리 등으로 result_json에 crop_ref 부재)는 모델 실패(no_candidates)와
        # 구분해 row_missing으로 귀속한다 — 데이터 정합 문제가 성능 수치를 오염시키지 않도록.
        row_missing = row is None
        row = row or {}
        top5 = row.get("item_top5") or []
        top5_labels = [t["label"] for t in top5]
        final = p["final_label"]
        draft_supply = row.get("supply")
        out.append(
            {
                **p,
                "top5_labels": top5_labels,
                "top1_sim": top5[0]["sim"] if top5 else None,
                "in_bank": final in bank,
                "label_bucket": (
                    "row_missing" if row_missing else label_bucket(final, top5_labels, bank)
                ),
                "draft_supply": draft_supply,
                "amount_raw": row.get("amount_raw", ""),
                "amount_bucket": (
                    None
                    if row_missing or p["supply"] is None
                    else amount_bucket(draft_supply, p["supply"])
                ),
            }
        )
    return out


def job_flags(enriched: list[dict]) -> dict[int, list[str]]:
    """잡 단위 이상 플래그를 계산한다. warp_suspect = 금액 무산출·0드리프트가 과반(≥2건)."""
    by_job: dict[int, list[dict]] = {}
    for r in enriched:
        if r["status"] == "included":
            by_job.setdefault(r["job_id"], []).append(r)
    flags = {}
    for jid, recs in by_job.items():
        amts = [r["amount_bucket"] for r in recs if r["amount_bucket"] is not None]
        bad = sum(b in ("zero_drift", "degenerate") for b in amts)
        flags[jid] = ["warp_suspect"] if bad >= 2 and bad * 2 >= len(amts) else []
    return flags


def oob_label_counts(enriched: list[dict]) -> list[tuple[str, int]]:
    """정답 라벨이 현재 뱅크에 없는 included 쌍의 빈도 내림차순 — 뱅크 추가 후보.

    성능 버킷(label_bucket)을 보지 않는다. 성능 측정은 *추론 시점 뱅크* 기준이어야 하지만
    뱅크 추가 후보는 *현재 뱅크* 기준이어야 한다 — 이미 든 라벨을 또 추가할 수는 없다
    (spec §1.2). 판정 불가 표본도 후보 집계에는 포함된다: "정답 라벨이 현재 뱅크에 없다"는
    추론 시점과 무관한 사실이다. 버킷을 보면 판정 불가 표본이 unevaluable로 귀속되는 순간
    후보 목록이 통째로 비어 개선 워크플로가 끊긴다.
    """

    def _answer(r: dict) -> str:
        # `answer` 생산자(Task 10)가 아직 없어 실제 행에는 이 키가 없다. 우선순위는 키
        # 존재 여부가 아니라 값의 유효성이어야 하므로 `dict.get(key, default)`이 아니라
        # `or` 체인을 쓴다 — `answer`가 존재해도 falsy(빈 문자열 등)면 canonical_label로
        # 넘어간다(H1: dict.get 형태는 키 존재만으로 폴백을 건너뛰는 false guard였다).
        return r.get("answer") or (r.get("canonical_label") or "").strip()

    counts = Counter(
        _answer(r)
        for r in enriched
        if r["status"] == "included" and _answer(r) and not r["in_bank"]
    )
    return counts.most_common()


def summarize(enriched: list[dict]) -> dict:
    """included 쌍의 핵심 지표를 집계한다 — 품목 지표는 평가 가능 쌍만 분모로 쓴다.

    금액 지표는 품목 평가 가능성과 무관하다(두 축이 독립이고 금액 버킷은 뱅크와 무관하다).
    label_buckets는 included 전체 분포를 유지한다 — unevaluable이 몇 건인지 보여야 한다.
    """
    inc = [r for r in enriched if r["status"] == "included"]
    ev = [r for r in inc if is_item_evaluable(r)]
    in_bank = [r for r in ev if r["in_bank"]]
    amounts = [r for r in inc if r["amount_bucket"] is not None]
    hit_sims = [r["top1_sim"] for r in ev if r["label_bucket"] == "ok" and r["top1_sim"]]
    miss_sims = [r["top1_sim"] for r in ev if r["label_bucket"] != "ok" and r["top1_sim"]]
    return {
        "n_included": len(inc),
        "n_item_evaluable": len(ev),
        "n_excluded": sum(r["status"] == "excluded" for r in enriched),
        "n_jobs": len({r["job_id"] for r in enriched}),
        "top1_hits": sum(r["label_bucket"] == "ok" for r in ev),
        "top5_hits": sum(r["label_bucket"] in ("ok", "top5_only") for r in ev),
        "in_bank_n": len(in_bank),
        "in_bank_top1": sum(r["label_bucket"] == "ok" for r in in_bank),
        "in_bank_top5": sum(r["label_bucket"] in ("ok", "top5_only") for r in in_bank),
        "amount_n": len(amounts),
        "amount_ok": sum(r["amount_bucket"] == "ok" for r in amounts),
        "label_buckets": Counter(r["label_bucket"] for r in inc),
        "amount_buckets": Counter(r["amount_bucket"] for r in amounts),
        "hit_sim_mean": sum(hit_sims) / len(hit_sims) if hit_sims else None,
        "hit_sim_min": min(hit_sims) if hit_sims else None,
        "miss_sim_mean": sum(miss_sims) / len(miss_sims) if miss_sims else None,
        "miss_sim_max": max(miss_sims) if miss_sims else None,
    }


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "0/0 (—)"


def _render_key_metrics(s: dict) -> list[str]:
    """핵심 지표 표 + 유사도 통계 줄을 렌더한다(render_report에서 순수 추출, M3)."""
    lines = [
        "## 핵심 지표",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 품목 top-1 (평가 가능 쌍 기준) | {_pct(s['top1_hits'], s['n_item_evaluable'])} |",
        f"| 품목 top-5 (평가 가능 쌍 기준) | {_pct(s['top5_hits'], s['n_item_evaluable'])} |",
        "| 정답이 뱅크에 존재(현재 뱅크 기준 · 평가 가능 쌍 분모) | "
        f"{_pct(s['in_bank_n'], s['n_item_evaluable'])} |",
        f"| in-bank 한정 top-1 | {_pct(s['in_bank_top1'], s['in_bank_n'])} |",
        f"| in-bank 한정 top-5 | {_pct(s['in_bank_top5'], s['in_bank_n'])} |",
        f"| 금액 일치 | {_pct(s['amount_ok'], s['amount_n'])} |",
        "",
        f"라벨 버킷: {dict(s['label_buckets'])}",
        f"금액 버킷: {dict(s['amount_buckets'])}",
    ]
    if s["hit_sim_mean"] is not None and s["miss_sim_mean"] is not None:
        lines += [
            "",
            f"top1 유사도 — 적중 평균 {s['hit_sim_mean']:.3f}(min {s['hit_sim_min']:.3f}) vs "
            f"미스 평균 {s['miss_sim_mean']:.3f}(max {s['miss_sim_max']:.3f})",
        ]
    return lines


def _render_job_table(
    enriched: list[dict], inc: list[dict], flags: dict[int, list[str]]
) -> list[str]:
    """잡별 요약 표를 렌더한다(render_report에서 순수 추출, M3)."""
    lines = [
        "",
        "## 잡별 요약",
        "",
        "| job | pairs | top1 | 금액ok | 플래그 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for jid in sorted({r["job_id"] for r in enriched}):
        recs = [r for r in inc if r["job_id"] == jid]
        ev = [r for r in recs if is_item_evaluable(r)]
        amts = [r for r in recs if r["amount_bucket"] is not None]
        # top-1을 k/n으로 적는다 — 0/n으로 적히면 판정 불가 잡이 전패로 오독된다.
        lines.append(
            f"| {jid} | {len(recs)} | "
            f"{sum(r['label_bucket'] == 'ok' for r in ev)}/{len(ev)} | "
            f"{sum(r['amount_bucket'] == 'ok' for r in amts)}/{len(amts)} | "
            f"{', '.join(flags.get(jid, [])) or '—'} |"
        )
    return lines


def render_report(enriched: list[dict], meta: dict) -> str:
    """분석 결과를 에이전트가 소비하기 좋은 마크다운 리포트로 렌더한다."""
    s = summarize(enriched)
    flags = job_flags(enriched)
    inc = [r for r in enriched if r["status"] == "included"]
    lines = [
        "# OCR 큐레이션 학습쌍 분석 리포트",
        "",
        f"- 동기화: {meta.get('fetched_at', '?')} · 잡 {s['n_jobs']}개 · "
        f"included {s['n_included']}쌍 · excluded {s['n_excluded']}쌍",
        f"- 뱅크: 임베딩 {meta.get('bank_size', '?')}개 / 라벨 {meta.get('bank_distinct', '?')}종",
        "",
    ]
    lines += _render_key_metrics(s)

    lines += ["", "## 뱅크 추가 후보 (out_of_bank 라벨)", ""]
    oob = oob_label_counts(enriched)
    if oob:
        lines += [f"- {label} ×{n}" for label, n in oob]
    else:
        lines.append("- 없음")

    misses = [
        r
        for r in inc
        if is_item_evaluable(r) and r["label_bucket"] in ("top5_only", "in_bank_miss")
    ]
    lines += ["", "## in-bank 리트리벌 미스", ""]
    for r in misses:
        lines.append(
            f"- {r['crop_ref']}: final={r['final_label']!r} draft={r['draft_label']!r} "
            f"sim={r['top1_sim']:.3f} [{r['label_bucket']}] top5={r['top5_labels']}"
        )
    if not misses:
        lines.append("- 없음")

    amt_fail = [r for r in inc if is_amount_failure(r)]
    lines += ["", "## 금액 실패", ""]
    for r in amt_fail:
        lines.append(
            f"- {r['crop_ref']}: draft={r['draft_supply']} final={r['supply']} "
            f"raw={r['amount_raw']!r} [{r['amount_bucket']}] (품목={r['final_label']!r})"
        )
    if not amt_fail:
        lines.append("- 없음")

    lines += _render_job_table(enriched, inc, flags)

    excluded = [r for r in enriched if r["status"] == "excluded"]
    if excluded:
        lines += ["", "## excluded (검수자가 학습 제외 — 크롭 불량 신호)", ""]
        lines += [
            f"- {r['crop_ref']}: final={r['final_label']!r} draft={r['draft_label']!r}"
            for r in excluded
        ]

    warp_jobs = [jid for jid, f in flags.items() if "warp_suspect" in f]
    lines += [
        "",
        "## 다음 액션",
        "",
        f"- 뱅크 추가 후보 {len(oob)}라벨 {sum(n for _, n in oob)}크롭 "
        "→ `pull-images`로 크롭 검수 후 뱅크 갱신",
        f"- warp 재검토 대상 잡: {warp_jobs or '없음'} "
        "→ warped.png를 시각 검수해 warp 실패 여부 확인",
        f"- 리트리벌 미스 {len(misses)}건 → 해당 라벨 뱅크 프로토타입 보강 검토",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# ssh fetch 글루 (원격 접속 — 단위테스트 비대상)
# ---------------------------------------------------------------------------


_BANK_PY = (
    "import numpy as np, json, os, collections; "
    "z = np.load(os.environ['SJMJ_ML_MODELS_DIR'] + '/bank.npz', allow_pickle=True); "
    "labs = [str(x) for x in z['lab']]; "
    "print(json.dumps({'size': len(labs), 'counts': collections.Counter(labs)}, "
    "ensure_ascii=False))"
)


def fetch_all(host: str, backend_env: str, worker_env: str, cache: Path) -> dict:
    """서버에서 training_pairs·result_json·뱅크 라벨을 동기화해 캐시 JSON으로 저장한다."""
    cache.mkdir(parents=True, exist_ok=True)
    pairs = parse_pairs_tsv(run_ssh(host, mysql_script(backend_env, PAIRS_SQL, raw=False)).decode())
    jobs = parse_jobs_tsv(run_ssh(host, mysql_script(backend_env, JOBS_SQL, raw=True)).decode())
    bank_script = f'{source_env(worker_env)}"$PYTHON_BIN" -c "{_BANK_PY}"'
    bank = json.loads(run_ssh(host, bank_script).decode())
    meta = {
        "fetched_at": datetime.now(UTC).astimezone().isoformat(timespec="seconds"),
        "host": host,
        "bank_size": bank["size"],
        "bank_distinct": len(bank["counts"]),
    }
    (cache / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    (cache / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1))
    (cache / "bank.json").write_text(json.dumps(bank, ensure_ascii=False, indent=1))
    (cache / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
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
    나열한다 — 이 함수는 spec §3-C 소비자 표(5곳)에 없다. plan Task 8 Step 5 Self-Review
    항목 ②가 "`_write_images_index`는 표시용이라 술어를 쓰지 않는다(의도)"라고 명시했다.
    `pull-images`로 당겨온 잡은 검수자가 크롭을 육안으로 보며 판정하므로, 판정 불가 행도
    같이 보여야 "이 행이 왜 판정 불가인지"를 그 자리에서 확인할 수 있다.
    """
    lines = ["# 큐레이션 크롭 검수 인덱스", ""]
    for r in enriched:
        if r["job_id"] not in job_ids:
            continue
        lines.append(
            f"- images/{r['crop_ref']}.png · final={r['final_label']!r} "
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
    pairs = json.loads((cache / "pairs.json").read_text())
    jobs = json.loads((cache / "jobs.json").read_text())
    bank = json.loads((cache / "bank.json").read_text())
    meta = json.loads((cache / "meta.json").read_text())
    return enrich_pairs(pairs, jobs, set(bank["counts"])), meta


def _failure_job_ids(enriched: list[dict]) -> list[int]:
    """pull-images 기본 대상 — 검수 대상 실패가 있는 잡 + excluded가 있는 잡.

    판정 불가만 있는 잡은 당기지 않는다(전 잡 폭주 방지). 재평가 전에는 금액 실패·excluded
    기반 검수 루프만 돌고, 품목 크롭 검수는 재평가 이후에 의미가 생긴다(spec §5).
    """
    return sorted(
        {r["job_id"] for r in enriched if r["status"] == "excluded" or is_item_failure(r)}
    )


def main(argv: list[str] | None = None) -> None:
    """서브커맨드(fetch/report/pull-images)를 파싱해 실행한다."""
    ap = argparse.ArgumentParser(prog="curation_report", description=__doc__)
    ap.add_argument("--host", default=env_or(ENV_SSH_HOST), help="ssh 호스트(별칭)")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE, help="로컬 캐시 디렉터리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="서버에서 pairs/jobs/bank 동기화")
    sub.add_parser("report", help="캐시 분석 → report.md + failures.jsonl")
    p_img = sub.add_parser("pull-images", help="실패 잡 크롭 동기화(기본: 실패 잡 전체)")
    p_img.add_argument("--jobs", type=int, nargs="*", help="특정 잡만")
    p_img.add_argument("--originals", action="store_true", help="원본 사진도 포함")
    args = ap.parse_args(argv)

    backend_env = env_or(ENV_BACKEND_ENV)
    worker_env = env_or(ENV_WORKER_ENV)

    if args.cmd == "fetch":
        meta = fetch_all(args.host, backend_env, worker_env, args.cache)
        print(f"동기화 완료 → {args.cache} ({meta['fetched_at']})")
        return

    enriched, meta = _load_enriched(args.cache)

    if args.cmd == "report":
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

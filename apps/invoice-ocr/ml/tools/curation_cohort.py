"""큐레이션 리포트의 시점 정합 판정 계층 — 코호트·평가 가능성 술어·재평가 유효성 게이트.

tools/curation_report.py에서 **순수함수와 상수만** 떼어낸 모듈이다(동작 변경 0의 기계적
분리). 리포트 본체는 fetch 글루·렌더까지 담아 파일 상한(800줄)에 닿는데, 이 계층은 IO 0·
부수효과 0이라 합성 데이터 단위테스트로 전량 닫히므로 경계가 자연스럽다.

handwriting/이 아니라 tools/에 두는 이유: 이 판정은 추론 경로가 아니라 분석 도구 계층의
관심사다(운영 워커는 import하지 않는다).

코어 규약 준수: stdlib 전용(paddle/numpy/pillow 불필요), 전부 순수함수.
"""

import json
from typing import Literal, NamedTuple, get_args

# 코호트 — 그 쌍의 품목 지표를 지금 해석할 수 있는지의 판정(spec §3-C).
# Cohort literal이 진실원이고 COHORTS는 get_args로 거기서 도출한다(bank_update.py의
# Scope/SCOPES 관용구와 동일) — sample_cohort의 반환 타입과 COHORTS가 구조적으로
# 드리프트할 수 없다. 다만 타입 힌트는 런타임에 강제되지 않으므로, sample_cohort의
# *실제 반환값 집합*이 이 치역과 일치하는지는
# test_sample_cohort_range_matches_cohorts_bijectively가 전수 입력 조합으로 별도 검증한다.
Cohort = Literal["reevaluated", "current_bank", "stale_bank", "unknown"]
COHORTS = get_args(Cohort)

# curation_enrich._item_bucket이 이 상수를 import해 쓴다 — 코호트 이름이 다른 모듈에서
# 원시 문자열 리터럴로 재등장하면 오타·개명이 타입 검사에 걸리지 않는다(M1).
REEVALUATED_COHORT: Cohort = "reevaluated"

# 쌍 단위 치역 — 잡의 시점 코호트에 정답 부재(no_label)를 더한 것이다. Cohort를 그대로 품는
# 관계는 test_pair_cohorts_extend_the_sample_cohorts_by_exactly_no_label이 고정한다.
PairCohort = Literal["reevaluated", "current_bank", "stale_bank", "unknown", "no_label"]
PAIR_COHORTS = get_args(PairCohort)

# 지표를 산출할 수 있는 코호트. reevaluated는 leave-invoice-out 수치이고 current_bank는 운영
# 추론 그대로지만 합산이 성립한다 — 스탬프가 현재 지문과 같다는 것은 그 잡을 처리한 뒤 뱅크가
# 한 번도 바뀌지 않았다는 뜻이고, 따라서 그 쌍의 자기 크롭도 같은 잡의 다른 행도 뱅크에 없다
# (들어갔다면 emb·keys가 바뀌어 지문이 달라진다). 이 등가는 지문 입력에서 keys를 빼는 변경으로
# 깨진다 — 그때는 합산도 함께 깨진다(spec §3-C).
ITEM_EVALUABLE_COHORTS = ("reevaluated", "current_bank")

# 사람이 크롭을 보고 개선할 여지가 있는 품목 미스 — 정답이 뱅크에 있는데 top-1을 놓친 경우다.
# out_of_bank(뱅크에 정답 자체가 없음)는 리트리벌 실패가 아니라 커버리지 문제라 뱅크 추가
# 후보 목록이 따로 낸다. no_candidates는 후보가 0건이라 대조할 것이 없다.
RETRIEVAL_MISS_BUCKETS = ("top5_only", "in_bank_miss")

# 품목 판정이 성립하지 않는 버킷 — 관심사가 다른 두 축의 합이다.
# unevaluable: 시점 판정 불가(재평가 없음 + 뱅크 스탬프 불일치/부재).
# row_missing: 데이터 정합 장애(재처리로 result_json과 training_pairs 조인이 어긋난 상태).
# is_item_failure는 DATA_INTEGRITY_FAILURE_BUCKETS만 따로 참조한다(아래) — 그래서 셋째
# 판정 불가 버킷이 TEMPORAL_UNEVALUABLE_BUCKETS에 추가돼도 is_item_failure의 기본값이
# 조용히 "실패 아님"으로 기울지 않는다.
TEMPORAL_UNEVALUABLE_BUCKETS = ("unevaluable",)
DATA_INTEGRITY_FAILURE_BUCKETS = ("row_missing",)
UNEVALUABLE_BUCKETS = TEMPORAL_UNEVALUABLE_BUCKETS + DATA_INTEGRITY_FAILURE_BUCKETS


def sample_cohort(
    *,
    job_retrieval_version: str | None,
    current_retrieval_version: str | None,
    has_reeval: bool,
) -> Cohort:
    """쌍 1건이 어느 코호트인지 판정한다 — 지표 산출 대상은 reevaluated·current_bank뿐이다.

    판정 근거는 파일 타임스탬프가 아니라 retrieval 지문이다(워커는 기동 시 뱅크를 1회만
    적재하므로 파일 mtime은 추론 시점을 말해주지 않는다, spec §1.1).

    인자를 키워드 전용으로 강제한다 — job_retrieval_version과 current_retrieval_version은
    동종 타입(str | None)이라 위치 인자로 넘기면 두 지문을 뒤바꿔도 예외 없이 unknown/
    stale_bank만 조용히 어긋난다.

    Args:
        job_retrieval_version: 그 잡 result_json의 retrieval_version. 스탬프 이전 잡은 None.
        current_retrieval_version: 현재 서버의 retrieval 지문. 못 얻었으면 None.
        has_reeval: 유효성 게이트를 통과한 재평가에 그 쌍이 있는지. stale 재평가는 이 인자가
            만들어지기 전에 걸러지므로, 여기서 강등되는 것이 아니라 애초에 False로 들어온다.

    Returns:
        "reevaluated"(재평가 있음) · "unknown"(스탬프 없음) · "current_bank"(스탬프 == 현재) ·
        "stale_bank"(그 외). 현재 지문을 못 얻은 경우도 stale_bank다 — "같다"고 볼 근거가 없다.
    """
    if has_reeval:
        return "reevaluated"
    if not job_retrieval_version:
        return "unknown"
    if job_retrieval_version == current_retrieval_version:
        return "current_bank"
    return "stale_bank"


def pair_cohort(
    *,
    answer: str,
    job_retrieval_version: str | None,
    current_retrieval_version: str | None,
    has_reeval: bool,
) -> PairCohort:
    """쌍 1건의 코호트 — 정답 부재(no_label)를 시점 판정보다 먼저 가른다.

    정답이 없으면 어느 시점의 뱅크로도 채점이 성립하지 않으므로, 재평가가 있어도 reevaluated로
    올리지 않는다. sample_cohort와 같은 이유로 인자는 키워드 전용이다(동종 타입 인접).

    Args:
        answer: 판정에 쓸 정답 라벨(strip된 canonical_label). 빈 문자열이면 no_label.
        job_retrieval_version: 그 잡 result_json의 retrieval_version. 스탬프 이전 잡은 None.
        current_retrieval_version: 현재 서버의 retrieval 지문. 못 얻었으면 None.
        has_reeval: 유효성 게이트를 통과한 재평가에 그 쌍이 있는지.

    Returns:
        "no_label" 또는 sample_cohort의 판정값.
    """
    if not answer:
        return "no_label"
    return sample_cohort(
        job_retrieval_version=job_retrieval_version,
        current_retrieval_version=current_retrieval_version,
        has_reeval=has_reeval,
    )


def is_item_evaluable(row: dict) -> bool:
    """그 쌍의 품목 판정이 성능 수치로 해석 가능한지 판정한다 — `label_bucket` 한 키만 본다.

    **전제**: 코호트 판정이 이미 `label_bucket`에 반영돼 있다고 가정한다 — `reevaluated`·
    `current_bank` 외 코호트(`stale_bank`·`unknown`)는 `label_bucket == "unevaluable"`로
    귀속된 뒤에야 이 함수의 반환값이 의미를 갖는다. 그 배선(코호트 판정 → label_bucket 대입)은
    아직 없다(Task 11의 `enrich_pairs`가 만든다) — 이 함수 자체는 코호트를 보지 않는다.

    소비자를 하나씩 고치면 빠뜨리는 자리가 생긴다(spec §3-C). 특히 `_failure_job_ids`가
    판정 불가를 실패로 세면 `pull-images`가 전 잡 크롭을 당긴다.
    """
    return row["label_bucket"] not in UNEVALUABLE_BUCKETS


def is_amount_failure(row: dict) -> bool:
    """금액 채점이 실패인지 판정한다 — 미기재(None)와 ok를 모두 걸러낸다.

    금액 실패 판정이 두 자리(`is_item_failure`·`render_report`의 금액 실패 목록)에 각기
    다른 문법으로 인라인되면, 한쪽에 예외가 붙는 순간 조용히 갈라진다 — 이 술어 하나로
    양쪽이 같은 정의를 공유하게 한다.
    """
    return row["amount_bucket"] not in (None, "ok")


def is_item_failure(row: dict) -> bool:
    """검수 대상 실패인지 판정한다 — 품목축·금액축·row_missing 세 조건의 **합집합**이다.

    ⚠️ 품목축 전용 판정에는 쓰면 안 된다. 리트리벌 미스 목록·잡별 top-1 분모 같은 품목축
    전용 자리에 이 술어를 그대로 끼우면 금액 실패·row_missing까지 품목 실패로 오집계돼
    분석 결론이 뒤집힌다. 품목축만 필요하면
    `is_item_evaluable(row) and row["label_bucket"] != "ok"`로 직접 조합해야 한다.

    row_missing은 시점 판정 불가가 아니라 **실재하는 데이터 정합 장애**(재처리로 result_json과
    training_pairs가 어긋난 상태)이므로 성능 분모에서만 빼고 운영 실패로는 남긴다 — 현행
    리포트도 이를 `_failure_job_ids`와 `main`의 report 분기가 만드는 `failures.jsonl`·
    `pull-images` 목록에 넣는다. 금액 버킷은 뱅크와 무관하므로(spec §8) 재평가 전에도
    유효한 검수 루프다.

    `status == "excluded"`(검수자가 학습 제외한 쌍)는 이 술어에 포함되지 않는다 —
    `_failure_job_ids`가 별도로 OR한다(spec §3-C의 소비자 표는 "`is_item_failure` 또는
    `excluded`"로 규정한다).
    """
    if row["label_bucket"] in DATA_INTEGRITY_FAILURE_BUCKETS:
        return True
    item_failed = is_item_evaluable(row) and row["label_bucket"] != "ok"
    return item_failed or is_amount_failure(row)


def partition_misses(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """품목 미스를 (사람이 볼 미스, 구조적 도달 불가)로 가른다.

    정답 라벨이 현재 뱅크에 **그 잡의 크롭으로만** 존재하면 전표 축이 그 행들을 전부 제외하므로
    재평가 preds는 정답에 도달할 수 없는데 in_bank는 참이다 → 버킷이 in_bank_miss가 되어
    구조적 도달 불가가 "리트리벌 실패"로 표기된다. 재평가 레코드의 has_peer가 명시적으로
    False일 때만 뺀다 — None(재평가 없음·라벨 PATCH로 판정 보류)은 도달 불가를 증명하지
    못하므로 목록에 남긴다(fail-open은 사람 눈에 더 보여주는 방향이라 안전하다).

    Args:
        rows: enriched 행들(호출자가 included로 이미 걸러 넘긴다).

    Returns:
        (misses, unreachable) — 둘의 합이 전체 품목 미스이며, 리포트는 앞을 나열하고
        뒤의 건수를 공개한다.
    """
    all_misses = [
        r for r in rows if is_item_evaluable(r) and r["label_bucket"] in RETRIEVAL_MISS_BUCKETS
    ]
    unreachable = [r for r in all_misses if r["reeval_has_peer"] is False]
    return [r for r in all_misses if r["reeval_has_peer"] is not False], unreachable


# 재평가 map에서 고를 한 벌 — after는 "지금 뱅크에서 어떤가"를 재기 때문이고,
# invoice 축은 같은 전표(같은 잡)의 다른 행이 답을 알려주는 낙관 편향을 제거하기 때문이다(§2.1).
REEVAL_SIDE = "after"
REEVAL_AXIS = "invoice"
# score.jsonl이 쌍마다 담는 side 어휘(bank_update.cmd_score가 before/after 두 벌을 쓴다).
# 레코드 수 검사와 조합 검사가 이 상수 하나를 공유한다 — 매직넘버(× 2)와 리터럴로 이중화하면
# side가 늘거나 바뀔 때 두 검사가 조용히 갈린다.
REEVAL_SIDES = ("before", "after")

# 게이트 실패 사유(정상 경로 — 예외가 아니다). 리포트가 사람에게 풀어 쓴다.
# ReevalReason literal이 진실원이고 REEVAL_REJECT_REASONS는 get_args로 거기서 도출한다
# (위 Cohort/COHORTS와 동일 관용구) — ReevalGate.reason의 타입과 이 상수가 구조적으로
# 드리프트할 수 없다. 다만 타입 힌트는 런타임에 강제되지 않으므로, reeval_gate의 *실제 반환
# 사유 집합*이 이 치역과 일치하는지는 test_reeval_gate_reject_reasons_match_the_literal_
# bijectively가 사유별 입력 전수로 별도 검증한다(오타·dead 사유 양방향).
ReevalReason = Literal[
    "no_meta",
    "no_fingerprint",
    "stale",
    "digest_mismatch",
    "bad_meta",
    "no_records",
    "record_count",
    "no_invoice_axis",
    "record_shape",
    "pair_count",
]
REEVAL_REJECT_REASONS = get_args(ReevalReason)

# 재평가 산출물 **회수** 상태 — fetch 글루가 meta["reeval_state"]에 적고 리포트가 읽는다.
# 게이트 사유(ReevalReason)와 다른 축이다: 이쪽은 "서버에서 무엇을 가져왔나", 저쪽은 "가져온
# 것을 왜 채택하지 않았나"다. 다만 score_meta.json 부재는 두 축이 같은 조건을 가리키므로
# 철자를 하나(`no_meta`)로 공유한다 — 두 철자로 부르면 소비자 분기가 하나 늘고, 생산자가
# 철자를 흘리는 순간 리포트가 "재평가 산출물이 없다"는 거짓 단정으로 샌다(M2).
# 어휘가 원시 문자열로만 존재하면 그 드리프트를 잡는 장치가 0이라 Literal로 결속한다
# (Cohort/COHORTS·ReevalReason/REEVAL_REJECT_REASONS와 같은 관용구).
ReevalState = Literal["absent", "no_meta", "present"]
REEVAL_STATES = get_args(ReevalState)

# 레코드가 반드시 문자열로 갖는 키 — 유일키 구성 요소이자 축 선택의 근거다.
REEVAL_RECORD_KEYS = ("side", "axis", "crop_ref")


class ReevalGate(NamedTuple):
    """재평가 채택 결과 — pairs가 None이면 '재평가 없음'이고 reason이 그 사유다."""

    pairs: dict[str, dict] | None
    reason: ReevalReason | None


def parse_reeval_jsonl(text: str) -> list[dict]:
    """회수한 score.jsonl을 파싱한다 — 한 줄이라도 깨지면 즉시 실패(parse_jobs_tsv 선례).

    조용한 오분류보다 낫다: 반쪽 파싱은 레코드 수 검사를 통과시켜 버릴 수 있다. JSON으로
    읽히기만 하는 줄(`123`)도 경계에서 막는다 — 게이트 안쪽까지 흘러가면 dict가 아닌 값에
    AttributeError가 나 원인이 파싱 경계에서 멀어진다.

    Raises:
        json.JSONDecodeError: 줄이 JSON으로 파싱되지 않을 때.
        ValueError: 줄이 JSON 객체가 아닐 때(리스트·숫자·문자열 등).
    """
    out = []
    for i, ln in enumerate(text.splitlines(), start=1):
        if not ln.strip():
            continue
        rec = json.loads(ln)
        if not isinstance(rec, dict):
            raise ValueError(f"재평가 score.jsonl {i}행이 JSON 객체가 아니다: {type(rec).__name__}")
        out.append(rec)
    return out


def _validate_reeval_records(records: list[dict]) -> None:
    """레코드의 필수 키·타입과 유일키를 검사한다 — 손상은 컨텍스트와 함께 즉시 실패시킨다.

    키 누락을 유일키 튜플의 None으로 흘리면 누락 2건이 "유일키 중복: (…, None)"으로 보고돼
    원인이 오보된다. 그래서 축 선택·조합 검사보다 앞에서 한 번에 검사하고, 이후 코드는
    `.get()`이 아니라 `[...]`로만 접근한다(접근 문법 이중화 방지).

    유일키(side·axis·crop_ref)뿐 아니라 **판정을 만드는 페이로드**(preds·top1_sim·label)도
    여기서 함께 검사한다(H1) — 유일키만 보고 통과시키면 `_truth_source`가 이 값들을 `.get()`
    fail-open으로 읽어 두 조용한 실패를 낸다: `preds` 키가 드리프트하면 재평가 쌍 전량이
    `top5_labels=[]` → `no_candidates`로 조용히 오분류되고(게이트는 통과하므로 리포트가
    "리트리벌이 후보를 0건 냈다"는 정반대 결론을 싣는다), `preds`는 있고 `top1_sim`이 없으면
    `_render_miss_list`의 포맷에서 `TypeError`로 리포트 생성이 통째로 죽는다. `has_peer`는
    검사 대상이 아니다 — 부재·None 모두 "판정 보류"라는 유효한 의미를 이미 가지므로
    `_truth_source`가 `.get()`을 유지한다.

    Raises:
        ValueError: 필수 키가 없거나 문자열이 아닌 레코드가 있을 때 · (side, axis, crop_ref)
            유일키가 중복될 때(덮어쓰기는 조용한 오분류다) · `preds`가 `list[str]`이 아닐 때 ·
            `preds`가 비어있지 않은데 `top1_sim`이 수치(bool 제외)가 아닐 때(불변식
            `preds 비어있음 ⟺ top1_sim is None`을 강제) · `label`이 str이 아닐 때.
    """
    seen: set[tuple[str, str, str]] = set()
    for i, r in enumerate(records, start=1):
        missing = [k for k in REEVAL_RECORD_KEYS if not isinstance(r.get(k), str)]
        if missing:
            raise ValueError(f"재평가 레코드 {i}행의 필수 키 결손: {missing} — 산출물 손상")
        key = (r["side"], r["axis"], r["crop_ref"])
        if key in seen:
            raise ValueError(f"재평가 레코드 유일키 중복: {key} — 덮어쓰기는 조용한 오분류다")
        seen.add(key)
        preds = r.get("preds")
        if not isinstance(preds, list) or not all(isinstance(p, str) for p in preds):
            raise ValueError(f"재평가 레코드 {i}행의 preds가 list[str]이 아니다 — 산출물 손상")
        top1_sim = r.get("top1_sim")
        is_numeric = isinstance(top1_sim, (int, float)) and not isinstance(top1_sim, bool)
        if preds and not is_numeric:
            raise ValueError(
                f"재평가 레코드 {i}행: preds가 비어있지 않은데 top1_sim이 수치가 아니다 — "
                "불변식(preds 비어있음 ⟺ top1_sim is None) 위반, 산출물 손상"
            )
        if not isinstance(r.get("label"), str):
            raise ValueError(f"재평가 레코드 {i}행의 label이 str이 아니다 — 산출물 손상")


def reeval_gate(
    *,
    records: list[dict],
    meta: dict | None,
    current_retrieval_version: str | None,
    jsonl_sha256: str | None,
) -> ReevalGate:
    """유효성 게이트를 통과한 재평가만 {crop_ref: 레코드}로 만든다(spec §3-C).

    하나라도 어긋나면 "재평가 없음"이다 — 예외가 아니라 정상 경로이며, 그 쌍들은 스탬프 기준
    분기로 간다. 사유 전량은 REEVAL_REJECT_REASONS이고 세 묶음이다.
      - meta 유효성: no_meta(유효성의 단일 게이트 — 없으면 score.jsonl이 있어도 없는 것) ·
        bad_meta(n_pairs가 음이 아닌 int가 아니다 — 레코드 수 검사를 세울 수 없다).
      - 시점·무결성: no_fingerprint(둘 중 하나라도 문자열이 아니다 — None == None을 "일치"로
        읽는 fail-open 차단) · stale(after 지문 ≠ 현재 지문) · digest_mismatch(meta가 적은
        score.jsonl 다이제스트 ≠ 회수분, 양쪽 문자열 요구).
      - 레코드 정합: no_records(채점 대상 0건 — 정상 산출물이다) · record_count(레코드 수 ≠
        n_pairs × len(REEVAL_SIDES) × len(axes)) · no_invoice_axis(axes가 전표 축을 주장하지
        않는다) · record_shape((side, axis) 조합 집합 불일치) · pair_count(전표 축 레코드가
        기대 쌍 수만큼 없다).

    인자는 키워드 전용이다 — current_retrieval_version과 jsonl_sha256은 인접 동종(str | None)
    이라 위치로 뒤바꿔 넘기면 예외 없이 stale/digest_mismatch로 재평가 전량이 폐기되고
    운영자는 잘못된 원인(스테일)을 보고 무의미한 재채점을 돌린다(sample_cohort와 같은 이유).

    검사 순서가 계약이다. 손상(예외) 검사를 완화 검사(record_count·record_shape) 뒤에 두면
    그 예외가 영원히 도달하지 않는다 — 중복 레코드는 총수를 늘려 record_count에 먼저 걸리고,
    전표 축 0건은 조합 집합을 깨 record_shape에 먼저 걸린다. 그래서 필수 키·유일키 검사는
    총수 검사보다 앞에, 전표 축 존재 검사는 조합 검사보다 앞에 둔다(예외를 고정한 테스트 참조).

    Raises:
        ValueError: 산출물 손상 3종만이다 — 필수 키(side·axis·crop_ref) 결손 ·
            (side, axis, crop_ref) 유일키 중복 · 레코드가 있고 axes가 전표 축을 주장하는데
            전표 축 레코드가 0건. meta 필드 타입 불일치·빈 산출물처럼 사람이 원인을 읽어야
            하는 경우는 예외가 아니라 사유 코드로 기각한다(정상 상태에서 도구가 죽지 않도록).
    """
    if not meta:
        return ReevalGate(None, "no_meta")
    versions = meta.get("retrieval_version")
    after = versions.get("after") if isinstance(versions, dict) else None
    if not isinstance(after, str) or not after or not isinstance(current_retrieval_version, str):
        return ReevalGate(None, "no_fingerprint")
    if after != current_retrieval_version:
        return ReevalGate(None, "stale")
    claimed_digest = meta.get("score_jsonl_sha256")
    if not isinstance(claimed_digest, str) or claimed_digest != jsonl_sha256:
        return ReevalGate(None, "digest_mismatch")
    n_pairs = meta.get("n_pairs")
    if not isinstance(n_pairs, int) or n_pairs < 0:
        return ReevalGate(None, "bad_meta")
    axes = list(meta.get("axes") or [])
    if REEVAL_AXIS not in axes:
        # axes가 전표 축을 주장하지 않는다 = 산출물 손상이 아니라 축이 하나뿐인 정상 산출물.
        # 게이트 실패는 예외가 아니라 정상 경로다(spec §3-C) — 사유 코드로 강등한다.
        return ReevalGate(None, "no_invoice_axis")

    _validate_reeval_records(records)
    picked = {
        r["crop_ref"]: r for r in records if r["side"] == REEVAL_SIDE and r["axis"] == REEVAL_AXIS
    }

    if len(records) != n_pairs * len(REEVAL_SIDES) * len(axes):
        return ReevalGate(None, "record_count")
    if not records:
        # 채점 대상 0건(n_pairs=0)은 손상이 아니라 정상 산출물이다 — cmd_score가 --scope 필터·
        # 크롭 부재로 valid를 0건까지 줄일 수 있고 score_summary([])·_pct(n=0)이 그 경로를
        # 의도적으로 처리해 빈 score.jsonl을 남긴다. 여기서 예외를 던지면 정상 상태에서
        # 리포트 전체가 죽는다.
        return ReevalGate(None, "no_records")
    if not picked:
        raise ValueError(
            f"재평가 산출물에 {REEVAL_SIDE}/{REEVAL_AXIS}(전표 축) 레코드가 없다 — 산출물 손상"
        )
    expected_shape = {(side, axis) for side in REEVAL_SIDES for axis in axes}
    if {(r["side"], r["axis"]) for r in records} != expected_shape:
        return ReevalGate(None, "record_shape")
    if len(picked) != n_pairs:
        # 어떤 쌍의 after/invoice가 빠지고 그 자리를 다른 조합이 채우면 총수·유일키·조합이
        # 다 맞아 통과하므로, 전표 축 레코드가 기대 쌍 수만큼 있는지를 별도로 센다.
        # ⚠️ 여기까지가 이 검사의 범위다: 같은 (side, axis) 조합으로 *낯선 crop_ref*가 치환된
        # 경우는 총수·유일키·조합·이 검사 모두를 통과한다(원래 쌍은 스탬프 분기로 강등되므로
        # 지표 오염은 없다 — fail-closed). 의도적 범위 밖이다 — 낯선 crop_ref 치환은 이미
        # fail-closed로 안전하게 수용되므로, 기대 crop_ref 집합과 대조하는 추가 강화의 이득이
        # 호출부에 그 집합을 새로 전달해야 하는 비용을 넘지 않는다(M3).
        return ReevalGate(None, "pair_count")
    return ReevalGate(picked, None)

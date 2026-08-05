# OCR 큐레이션 데이터 분석 루프

배포 서버(macmini)에 쌓이는 학습 큐레이션 데이터(training_pairs + 크롭 이미지)를
주기적으로 분석해 OCR 정확도 개선 방향을 도출하는 절차.
사용자가 명시적으로 지시할 때 AI 에이전트(Claude Code)가 수행한다.

도구: `apps/invoice-ocr/ml/tools/curation_report.py` (stdlib 전용, ssh로 서버 접근).
원격 접속값은 env 주입 — 기본값은 현행 배포 관례(`ml/.env.example` 참조).

## 절차

모든 명령은 `apps/invoice-ocr/ml`에서 실행한다.

> 선행: 운영 DB에 `db/migration_009_training_pairs_exclusion_reason.sql`이 적용돼 있어야 한다
> (`fetch`가 `training_pairs.exclusion_reason`을 읽는다).

```bash
# 0. (선택·권장) macmini에서 재평가 산출 — 현재 뱅크로 다시 retrieval해 지표를 복원한다.
#    이것을 돌리지 않으면 스탬프 이전 잡은 전부 판정 불가(unevaluable)로 나온다.
#    절차는 docs/runbooks/ocr-bank-update.md 4단계(--scope all) 참조.

# 1. 서버에서 최신 데이터 동기화
#    (training_pairs + result_json + 교정 이력 + 뱅크 라벨 + 현재 지문 + 재평가)
uv run python -m tools.curation_report fetch

# 2. 분석 리포트 생성 → results/curation/report.md + failures.jsonl
uv run python -m tools.curation_report report

# 3. (리포트의 실패 건 검수 시) 실패 잡의 크롭·warp 이미지 동기화
uv run python -m tools.curation_report pull-images            # 실패 잡 전체
uv run python -m tools.curation_report pull-images --jobs 39 44 --originals
```

> [!NOTE]
> **스탬프가 없거나(`unknown`) 현재 지문과 다른(`stale_bank`) 잡은, 재평가를 돌리지 않는 한
> `pull-images`가 품목 실패로 고르지 못한다**(정상 동작) — 위 0번 코호트에서 `unevaluable`인
> 쌍은 **품목 실패로는** 실패 목록에 오르지 않는다(금액 실패로는 오른다 — 아래 0번 참조).
> 배포 후 새로 추론된 잡은 스탬프가 현재 지문과 같아 `current_bank`가 되므로, 재평가 없이도
> 품목 실패로 선정된다. 특정 잡을 강제로 확인하려면 `--jobs <job_id...>`를 코호트와 무관하게
> 명시한다.

> [!WARNING]
> **릴리스 배포는 재평가를 무효화한다.** retrieval 지문에는 배포 코드 SHA가 들어간다(전처리·
> 임베딩·후보 선택 코드가 결과 경로의 일부이고 `deploy.yml`이 배포마다 워커를 재시작한다).
> 따라서 프론트·문서만 바뀐 릴리스여도 지문이 바뀌어 과거 재평가는 `stale`로 기각되고 과거
> 잡은 `stale_bank`가 된다 — **배포 후에는 `bank_update score --scope all`을 다시 돌린다.**
> 이것은 버그가 아니라 정직한 표현이다(코드가 바뀌면 추론 경로가 바뀐다).

## 이 리포트가 재는 것

버킷 해석에 들어가기 전에 **경계를 먼저 읽는다** — 아래 "리포트 읽는 법" 0번이 분모를 먼저
읽으라고 하는 것과 같은 이유이고, 이 절은 그 분모의 바깥 경계를 정한다.

### "실패"의 네 층위

도구와 리포트가 쓰는 "실패"는 한 가지가 아니다. **`failures.jsonl`에 실리는 실패 쌍은 top-1
지표의 정의가 아니다** — 금액 실패와 정합 장애가 함께 들어 있다.

```
검수 대상 실패 (쌍)  ← failures.jsonl
├─ 품목 실패    ← top-1 지표의 정의. 평가 가능 코호트 한정
├─ 금액 실패    ← 코호트 무관
└─ 정합 장애    ← row_missing. 지표 분모 밖, 운영 실패로는 남음

실패 잡  ← pull-images 대상 선정 기준. 위 실패가 있는 잡 + excluded가 있는 잡
```

**실패 잡은 지표의 모집단이 아니다.** 지표는 확정된 전 잡의 전 쌍에서 나온다
(`curation_enrich.PAIRS_SQL`에 필터가 없다) — 사람이 확정에 성공한 잡이 분석에서 빠지는 일은
없다. 실패 잡은 어느 잡의 크롭을 내려받을지 고르는 기준일 뿐이다.

| 개념           | 코드                                                                            | 리포트 헤딩                                             |
| -------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------- |
| 검수 대상 실패 | `curation_cohort.is_item_failure`                                               | 없음 (`failures.jsonl`)                                 |
| 품목 실패      | **전용 식별자 없음** — `is_item_evaluable(row) and row["label_bucket"] != "ok"` | `in-bank 리트리벌 미스`·`뱅크 추가 후보`(각각 부분집합) |
| 금액 실패      | `curation_cohort.is_amount_failure`                                             | `금액 실패`                                             |
| 정합 장애      | `curation_cohort.DATA_INTEGRITY_FAILURE_BUCKETS`                                | 없음                                                    |
| 실패 잡        | `curation_report._failure_job_ids`                                              | `다음 액션`의 "실패 잡 수"                              |

> [!WARNING]
> **`is_item_failure`를 품목축 판정에 쓰지 않는다.** 이름과 달리 세 축의 합집합이라, 리트리벌
> 미스 목록이나 잡별 top-1 분모 자리에 그대로 끼우면 금액 실패·정합 장애까지 품목 실패로
> 집계돼 결론이 뒤집힌다. 품목축만 필요하면 위 표의 조합식을 직접 쓴다.

### 이 리포트가 읽는 소스

`fetch`가 서버에서 당겨 캐시에 굳히는 것은 여섯이다.

| 소스                         | 원천                | 담기는 것                                                        |
| ---------------------------- | ------------------- | ---------------------------------------------------------------- |
| `pairs.json`                 | `training_pairs`    | 확정 잡의 크롭 좌표·정식 라벨·배제 상태와 사유                   |
| `jobs.json`                  | `ocr_jobs`          | 초안(`result_json`)의 top-5·금액, 원본 경로                      |
| `corrections.json`           | `ocr_corrections`   | 잡 단위 행 수지(초안·사람 추가·사람 폐기·확정)와 원본 경로       |
| `label_sources.json`         | `ocr_corrections`   | 확정 행별 UI 조작 출처(`correction_json.lines[].label_source`)   |
| `bank.json`                  | `bank.npz`          | 현재 뱅크가 보유한 라벨                                          |
| `reeval.jsonl` + `meta.json` | macmini 재평가 산출 | 현재 뱅크 기준 재retrieval 결과와 그것을 해석하는 retrieval 지문 |

`corrections.json`의 모집단은 **확정 잡 전량**이다(백엔드 `_UNCONFIRMED_WHERE`의 부정을
미러링한다 — 명세서 연결·교정 이력·학습 후보 쌍 중 하나라도 있으면 확정이다) — 학습 후보 쌍이
0개인 잡, 즉 행검출이 전멸한 잡도 여기엔 남는다.

### 소스에 없는 것은 보이지 않는다

**확정되지 않은 잡은 여기 없다.** 학습 후보 쌍은 확정 시점에 만들어지므로 강등 잡과 미확정
잡은 `training_pairs`에 한 줄도 남기지 않는다. 그쪽을 보는 곳은 처리 관측
(`/curation/pending`)이고, 두 화면의 모집단은 배타적이다 — 관측 목록은 거래명세서·교정 이력·
학습 후보 쌍이 **모두 없는** 잡만 낸다. 읽기 전용이라 거기서 고칠 수는 없다(ADR 0009).

**교정 이력(`ocr_corrections`)에서 읽는 것은 행 수지와 조작 출처 둘이다.** 사람이 행을 몇 개
보태고 몇 개 버렸는지는 `## 행 수지` 절이, 라벨을 top-1 그대로 뒀는지 후보에서 골랐는지 직접
쳤는지(`correction_json.lines[].label_source`)는 `## 조작 출처` 절이 낸다.

**`label_source`는 클라이언트 주장이며 서버가 검증하지 않는다** — 그것이 이 필드의 정의다
(서버가 추론으로 덮으면 재학습 분석에서 관측값과 추정값을 구분할 수 없게 된다,
`services/ocr_correction.py`). 초안과의 정합성 모순(`top1_kept`인데 라벨이 바뀐 행 등)은
`correction_json`의 draft/final로 사후 감사한다.

잡이 삭제돼 `job_id`가 풀린 고아 교정(FK가 `ON DELETE SET NULL`)은 구조상 빠진다 — 귀속할
잡이 없어 행 수지의 단위가 없다. 그래서 이 캐시의 행 수는 `ocr_corrections` 총 행 수와
일치하지 않을 수 있다(정상).

### top-1 분모에서 빠지는 것

top-1은 **전표 단위 정확도가 아니다.** 분모에서 빠지는 넷은 성격이 다르며, 결함은 첫 줄
하나뿐이다.

| 빠지는 것               | 근거                                             | 성격                                                                                                                                                            |
| ----------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 사람이 손으로 추가한 행 | 크롭 좌표가 없는 줄은 학습 후보 쌍이 되지 않는다 | **눈이 머는 것 — 다만 이제 크기는 보인다.** 분자·분모 어디에도 없는 것은 그대로지만, 몇 행이 빠졌는지는 리포트 `## 행 수지` 절의 "행검출 가시 범위"가 낸다(#72) |
| 연속행                  | 애초에 크롭도 쌍도 만들지 않는다                 | 빠지는 게 옳다 — 품목이 아니다                                                                                                                                  |
| `excluded` 쌍           | 학습에서 뺀 쌍이라 해석 비대상                   | 옳으나 배제율 자체가 신호다(읽는 법 4번)                                                                                                                        |
| 평가 불가 코호트        | 시점 정합이 안 돼 지금 채점할 근거가 없다        | 옳다(읽는 법 0번)                                                                                                                                               |

## 리포트 읽는 법 (에이전트 체크리스트)

0. **표본 구성**(핵심 지표 위) — 분모를 먼저 읽는다. 코호트는 그 쌍의 품목 지표를 지금
   해석할 수 있는지의 판정이며, 근거는 파일 타임스탬프가 아니라 retrieval 지문이다
   (뱅크 행 + 모델 파일 + 배포 코드 SHA). 워커는 기동 시 뱅크를 1회만 적재하므로 파일
   mtime은 추론 시점을 말해주지 않는다.
   - `reevaluated` — 현재 뱅크로 재retrieval한 쌍. **지표 산출 대상.**
   - `current_bank` — 스탬프가 현재 지문과 같아 운영 추론이 그대로 유효한 쌍. **지표 산출 대상.**
   - `stale_bank` — 구 retrieval 상태 + 재평가 없음. 판정 불가(`unevaluable`).
   - `unknown` — 스탬프 이전 잡 + 재평가 없음. 판정 불가.
   - `no_label` — `canonical_label`이 없어 정답이 없는 쌍. 판정 불가. `final_label`로
     폴백하지 않는다.
   - `excluded` — 학습에서 뺀 쌍(해석 비대상). 사람 배제와 기계 자동 배제가 함께 들어가며
     소유 축 분해는 아래 4번이 낸다.

   판정 불가 표본은 **품목** 지표의 분자·분모에서 빠지고, **품목 실패로는**
   `failures.jsonl`·`pull-images` 대상에도 들어가지 않는다
   (`curation_cohort.is_item_evaluable`이 이 코호트를 걸러낸다). 그래서 재평가를 돌리지
   않은 상태에서는 **품목** 핵심 지표(top-1/top-5)가 `0/0`으로 나올 수 있다 — 수치가
   사라진 것이 아니라, 그 수치에 애초에 근거가 없었다는 사실이 드러난 것이다.

   **금액 지표·금액 실패·`excluded`는 코호트와 무관하게 그대로 집계·수집된다.**
   `curation_enrich.summarize`의 `amounts`는 `is_item_evaluable` 필터 없이 `included`
   전체에서 뽑아 핵심 지표 표의 "금액 일치" 행이 되고, `curation_cohort.is_item_failure`는
   `is_amount_failure`를 코호트 확인 없이 OR한다. 즉 판정 불가 잡이라도 그 쌍이 금액
   버킷에서 실패면 `failures.jsonl`에 실리고 그 잡은 `pull-images` 대상이 된다 — 뱅크
   추가 후보 집계도 마찬가지로 코호트와 무관하게 현재 뱅크 기준으로 돈다.

   "표본 구성" 표 바로 아래에 **재평가 상태 알림** 한 줄이 붙는다(채택 / 재평가 없음 —
   `no_meta`·기각 사유 등). **현재 지문을 확정하지 못한 상태**(리포트가
   `현재 retrieval 지문: 미확정`으로 표시)라면 재평가를 돌려도 채택되지 않는다 —
   먼저 `fetch`를 다시 실행해 지문을 확보한 뒤에 재평가를 시도한다.

1. **핵심 지표** — 이전 리포트와 비교해 top-1/top-5/금액 일치 추이를 확인한다
   (리포트는 `results/curation/`에 덮어써지므로 추이 비교가 필요하면 이전 값을 기록해 둘 것).
   위 0번의 표본 구성이 그때그때 다를 수 있으므로, 분모가 다른 리포트끼리 수치만 직접
   비교하지 않는다.
2. **라벨 버킷** 우선순위:
   - `out_of_bank`: 뱅크에 정답이 없어 구조적으로 못 맞춘 것. **뱅크 추가가 유일한 해결책**
     — 해당 크롭을 `pull-images`로 받아 품질 확인 후 뱅크 갱신 작업으로 잇는다.
   - `top5_only`: 후보엔 있었음 — 뱅크 프로토타입 보강·리랭킹 여지.
   - `in_bank_miss`: 뱅크에 있는데 후보 밖 — 크롭 품질(우측 잘림·옆칸 잉크 침입) 또는
     동일 라벨의 필체 변형 부족을 의심하고 크롭을 눈으로 확인한다.
   - `no_candidates`: 후보가 0건 — 리트리벌이 아예 돌지 않은 것이다(뱅크 미적재·크롭 부재
     의심). 라벨 문제가 아니므로 뱅크 보강이 아니라 추론 경로를 확인한다.
   - `row_missing`: 학습쌍의 crop_ref가 현재 result_json에 없음(재처리 등) — 모델 문제가
     아니라 데이터 정합 문제이므로 성능 해석에서 제외하고 원인을 별도 확인한다.
   - `unevaluable`: 위 0번 코호트가 판정 불가로 격리한 쌍 — 시점 정합이 안 돼 지금 채점할
     근거가 없다는 뜻이며, `row_missing`(데이터 정합 문제)과는 관심사가 다르다.

   "in-bank 리트리벌 미스" 목록은 **도달 불가**(재평가 `has_peer=False` — 정답 라벨이 그
   잡의 크롭으로만 뱅크에 있어 전표 축 제외 후 후보가 남지 않는 쌍)를 목록에서 빼고 건수만
   낸다. 개선 여지가 없는 쌍이므로 뒤지지 않는다. 같은 절의 "현재 뱅크 보유" 커버리지 줄은
   라벨 있는 `included` 전체 기준이라 위 핵심 지표(평가 가능 쌍 분모)와 분모가 다르다.

3. **금액 버킷**:
   - `zero_drift`(0으로 읽음)가 잡 단위로 몰리면 `warp_suspect` 플래그가 붙는다 —
     해당 잡 `warped.png`를 반드시 눈으로 확인. 쿼드 오검출(배경 포함·오프셋)이면
     템플릿 좌표(ITEM_X/AMOUNT_X)가 통째로 어긋난 것.
   - `degenerate`(`!!!` 등): MLX-VLM 퇴화 출력. 재시도는 이미 걸려 있으므로
     (`handwriting/amount_read.py`의 `read_amount_with_retry`) 여기 남은 건은 **재시도까지
     실패한 것**이다 — `amount_raw`가 시도별 원문을 `→`로 join하므로 그것부터 본다.
   - `sign_mismatch`: 부호만 상이(`draft == -final`) — 값 자체는 읽었으므로 크롭·필체가
     아니라 부호 처리(반품·차감 행) 쪽을 본다.
   - `misread`: 행 밴드와 손글씨 세로 어긋남(오프바이원) 또는 자릿수 오독 — warp와
     인접 행 금액을 같이 봐야 구분된다.
4. **배제 집계** — `excluded`는 사유로 두 축을 가른다.
   - **기계 자동 배제**(사유 `blank_crop`) — 빈 크롭 가드가 잡은 것. 행검출 이슈의 직접 신호다.
   - **사람 배제**(사유 없음) — 검수자가 뺀 것. 종전 두 갈래(크롭 불량 / 원본에 품목 미기재)가
     여기 섞여 있고 사유 선택 수단이 없어 분리되지 않는다(ADR 0004 부채).
     섞어 세지 않는다 — 기계 배제율은 가드의 계측기, 사람 배제율은 크롭 품질·원본 결손의 신호다.
5. **오탐 관측치** — `included`인데 사유가 `blank_crop`인 쌍은 사람이 자동 배제를 되돌린 것으로,
   그 개수가 곧 이 가드의 오탐률 관측치다(ADR 0006).
6. **행 수지** — `## 행 수지` 절. 손실이 **두 단계로** 분해돼 있고 두 줄의 분모가 다르다.
   섞어 읽지 않는다.
   - `행검출 가시 범위` = 학습 후보가 된 행 / 사람이 인정한 행(`n_lines` / `confirmed_rows`) —
     이 슬라이스가 새로 여는 축이다. 분모가 사람이 인정한 실제 행이고, 여기서 빠진 몫이
     **행검출이 놓친 행**이다.
   - `└ 그중 판정 가능` = 판정 가능 쌍 / `n_lines` — 기존 코호트·배제 축이다(위 0번·4번).
     여기서 빠진 몫은 행검출 실패가 아니라 시점 정합·학습 제외·정합 장애다.
   - 두 줄을 한 줄로 합쳐 읽으면(판정 가능 쌍 / `confirmed_rows`) 뱅크 시점 문제와 학습
     제외까지 행검출 누락으로 오독한다.
   - `학습 후보 쌍 N개(수지 known 잡 한정)`과 `n_lines`는 소스가 다르다(`training_pairs` vs
     교정 이력). 크게 어긋나면 재처리·삭제 흔적이므로 원인을 따로 확인한다. 이 쌍 수는 배제
     쌍도 포함한다(배제는 confirm 이후의 상태 변경이라 `n_lines`와 같은 축이다). 또한 두 수
     모두 행 수지가 known인 잡만 센다.
   - `행 수지 미상 N잡`은 합계 밖이다. `교정 이력 없음`은 구 데이터라 정상이고,
     `교정 JSON 결손`은 데이터 결손(버그 의심)이라 조치가 다르다.
   - 잡별 요약 표의 `초안 / +행 / -행` 3열과 `row_gap` 플래그가 잡 단위 신호다(`row_gap`은
     사람이 옮긴 행이 2개 이상이면서 확정 행의 절반 이상일 때 켜진다). 3열의 `?`는 행 수지
     미상이고, `pairs(incl)` 0은 그 잡에 included 쌍이 없다는 뜻이다(행검출 전멸 후보 — 배제쌍만 있는
     잡도 0으로 찍히므로, 머리말의 `쌍 보유`(배제쌍 포함 기준)와 다를 수 있다).
     `top1`·`금액ok`의 `—/0`은 분모가 0이라 판정할 근거가 없다는 표기다(쌍이 없거나 전부
     판정 불가).
   - `row_gap` 잡의 후속 조치는 **원본 사진과 행검출 결과 대조**다. 쌍 0개·크롭 0개 잡에서도
     원본은 받아진다:

     ```bash
     uv run python -m tools.curation_report pull-images --jobs <job_id...> --originals
     ```

     받은 원본은 `results/curation/images/job-<id>/original.jpg`에 저장된다. 회수 성패는
     요약 줄의 `원본 N/M 회수 · K건 실패`로 확인한다 — 원본을 못 받은 잡은 그 자리에 파일이
     없다.

7. **조작 출처** — `## 조작 출처` 절. top-1 적중률이 과소평가하는 축이다(top-1이 틀려도 사람이
   후보 칩에서 골랐다면 모델이 일한 것이다).
   - 분모 사다리를 먼저 읽는다. **사람 폐기·추가 행은 분모 밖**이다 — `lines[]`에 없어 조작
     출처가 존재조차 하지 않는다. `미기록`은 원인을 나누지 않는다(도입 전·미전송·오타 키
     유실이 섞여 있다). `초안 ?행`의 `?`는 행 수지 미상 잡이 섞였다는 표기다.
   - `rank`는 **0-based**다. `rank 0` = top-1 바로 그 후보이며, 0건 rank 행도 전량 인쇄된다
     ("뒤쪽 rank에서 아무도 안 골랐다"가 곧 top-5 확대 무용의 근거다).
   - **재학습 판단 분기** (`### 출처 × 품목 버킷` 절의 headline과 함께 읽는다):
     - 후보 칩 선택 표본이 하한(10건) 미만이면 **판단하지 않는다.** 리포트가 경고 줄로 알린다.
     - 하한 이상 + top-1 미적중분에서 `candidate_picked`가 과반 → **임베딩 품질 축**
       (재학습·리랭킹 검토). 후보엔 있는데 top-1을 못 맞히고 있다는 뜻이다.
     - 하한 이상 + `manual_typed` + `new_item_created`가 과반 → **뱅크 부족 축**
       (품목 어휘 동기화 루프, `bank_update`). 후보 자체가 쓸모없었다는 뜻이다.
     - rank가 뒤쪽(0-based rank 3 이상 = 4번째 후보 이후)에 몰리면 → 후보 폭 확대·리랭킹 검토.
       rank 0–1에만 몰리면 넓혀도 소용없다.
   - `### 출처 × 품목 버킷` headline은 **두 수치**를 낸다.
     `top-1 미적중인데 후보 칩에서 고름`(AC 수치 · 분모=`label_bucket != ok` 전량)에는
     `out_of_bank`(정답이 뱅크에 없음)·`no_candidates`(후보 칩 0건)가 섞여 있어 후보 칩
     효용을 과소평가한다. `└ 정답이 뱅크에 있던 미스 한정`(보조 지표 · 분모=리트리벌 미스
     한정)이 후보 칩이 실제로 도울 수 있었던 몫이다 — 재학습 판단은 **좁힌 쪽**을 본다.
   - `⚠ 후보 칩 선택 표본 …건(하한 10)`이 뜨면 rank 분포는 아직 판단 근거가 아니다.
   - `⚠ 관측 rank가 기본 범위를 넘었다`가 뜨면 백엔드 `app/schemas/ocr.py`의 TOP_K가 늘어난
     것이다 — `DEFAULT_RANK_SLOTS`를 함께 올려야 0건 rank 행이 전량 인쇄된다.
   - `⚠ 알 수 없는 조작 출처`가 뜨면 백엔드 `app/schemas/ocr.py`의 허용 어휘가 늘었을 수 있다.
     그 값들도 분모에는 포함돼 있다(조용히 버리지 않는다).
   - `### 출처 × 품목 버킷`의 모집단은 **평가 가능** 행이다(핵심 지표와 같은 잣대).
     `top1_kept`인데 `ok`가 아닌 셀이 이 표의 핵심 관측이다 — 사람이 틀린 top-1을 그대로 뒀다.

## 검수 시 제외 기준

큐레이션 검수 화면에서 다음 행은 `excluded`로 내린다.

- **원본에 품목명이 없는 행** — 타이어처럼 관례상 품목명을 생략하고 `단가 × 수량`만
  적는 전표가 있다. 검수자가 맥락으로 정답을 채워 넣어도 이미지에는 근거가 없어
  학습쌍이 성립하지 않는다. 모델은 이런 행에서 품목칸 대신 수량·단가 숫자를 크롭하므로,
  included로 두면 리트리벌 실패로 집계되어 지표를 왜곡한다(2026-07-29 잡 53).
- **크롭 불량** — 행 오프셋·옆칸 침입 등으로 손글씨가 온전히 담기지 않은 행.

자동 판별은 하지 않는다 — 잡 53은 숫자를 잘못 크롭했는데도 top-1 유사도가 0.862로
적중 평균(0.845)을 웃돌아, 미확신 신호로 걸러지지 않는다.

빈 크롭은 **`BLANK_INK_MAX`가 확정된 뒤에는** 사람이 제외하지 않아도 된다 —
`tools/blank_crop_report.py apply`가 자동 배제한다. 임계 확정 전까지는 종전대로 사람이
제외한다. 자동 배제가 틀렸다고 판단되면 큐레이션 화면에서 "포함"으로 되돌린다
(되돌린 쌍은 재판정에서 영구 보호된다).

## 품목 어휘 발산 진단

큐레이션을 통과한 정식 라벨이 자동완성 사전(`item_suggestions`)에 있는지 본다.
등록은 검수완료(`POST /api/curation/jobs/{job_id}/review`)와 검수완료된 잡의 쌍 PATCH가
자동으로 하므로(ADR 0008), **결과가 0행인 것이 기대 상태**다.
0행이 아니면 아래 원인 표를 위에서부터 확인한다 — **등록 경로 회귀는 원인 중 하나일 뿐이고,
사람이 사전 항목을 지우거나 이름을 바꾸기만 해도 잔여가 생긴다.**

**실행 위치는 운영 macmini**(`ssh macmini`)의 `mysql` 클라이언트다 — 접속값과 대상 DB명은
`~/.sjmj-ai/backend.env`의 `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER`/`DB_PASS`에서 읽는다
(DB명 하드코딩 금지 — 런타임/백업 DB 발산 방지).

> 선행: 운영 DB에 `db/migration_010_sync_item_vocabulary.sql`이 적용돼 있어야 한다
> (미적용이면 기존 발산분이 그대로 보고된다 — 원인 표 첫 행).

<!-- diagnostic-sql -->

```sql
SELECT REGEXP_REPLACE(COALESCE(tp.canonical_label, ''), '^[[:space:]]+|[[:space:]]+$', '') AS label,
       COUNT(*) AS pairs
FROM training_pairs tp
LEFT JOIN item_suggestions it
       ON it.item_name = REGEXP_REPLACE(COALESCE(tp.canonical_label, ''),
                                        '^[[:space:]]+|[[:space:]]+$', '')
          COLLATE utf8mb4_0900_ai_ci
JOIN ocr_jobs j ON j.id = tp.job_id AND j.curation_reviewed = 1
WHERE tp.status = 'included'
  AND it.id IS NULL
  AND REGEXP_REPLACE(COALESCE(tp.canonical_label, ''), '^[[:space:]]+|[[:space:]]+$', '') <> ''
GROUP BY label
ORDER BY pairs DESC;
```

> **별칭을 `canonical_label`이 아니라 `label`로 두는 것이 필수다.** MySQL은 `GROUP BY`의 비한정 식별자를 select 별칭보다 **FROM 절 컬럼에서 먼저** 찾는다. 별칭을 `canonical_label`로 두면 `GROUP BY canonical_label`이 원문 컬럼으로 해석되어 공백 변형마다 그룹이 쪼개진다.

| 원인                                                                                                 | 확인 방법                                                                                                                 | 처치                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `db/migration_010_sync_item_vocabulary.sql` 미적용 (기존 발산분이 그대로 남음)                       | `SELECT applied_at FROM schema_migrations WHERE filename = 'migration_010_sync_item_vocabulary.sql'` — 행이 없으면 미적용 | `scripts/migrate-db.sh`로 적용한다. 이 잔여는 등록 경로 회귀가 아니라 미적용 상태다                                                                                                           |
| 사람이 자동 등록분을 사전에서 삭제·개명 (`DELETE`/`PUT /api/items/{id}`)                             | 보고된 라벨이 과거 등록됐던 이름인지, 품목 관리 화면 이력으로 확인                                                        | 의도한 삭제면 그대로 둔다 — 이 잔여는 정상이다. 되살리려면 해당 쌍의 정식 라벨을 큐레이션 화면에서 다시 저장한다(PATCH가 등록 트리거)                                                         |
| 서비스 밖 writer가 쌍을 `included`로 되돌림 (`apps/invoice-ocr/ml/tools/blank_crop_report.py apply`) | 해당 잡의 `curation_reviewed`와 쌍의 `reviewed_at` 확인                                                                   | `apply`는 되돌린 쌍이 있는 잡을 `curation_reviewed = FALSE`로 함께 되돌리므로 보통 이 진단에 잡히지 않는다. 잡혔다면 그 결합이 끊긴 것 — `blank_crop_report`의 un-review 문장을 먼저 확인한다 |
| 배포 컷오버 창에서 누락 (migration 적용 ~ 백엔드 재시작 사이의 검수완료)                             | 잡의 검수 시각이 직전 배포 시각대인지 확인                                                                                | 해당 쌍의 정식 라벨을 큐레이션 화면에서 **다시 저장**한다. 검수완료 버튼은 이미 검수된 잡에서 `disabled`이므로 재클릭으로는 복구되지 않는다                                                   |
| 등록 트리거 회귀                                                                                     | 위 넷이 아니면 이것이다                                                                                                   | `CurationService.mark_reviewed`·`patch_pair`의 등록 경로와 `app/routers/curation.py`의 `ItemRepository` 주입을 점검한다 (ADR 0008, #40)                                                       |

- **`COLLATE`는 목적지 유니크 인덱스의 collation(`utf8mb4_0900_ai_ci`)으로 맞춘다 —
  방향이 결과를 바꾼다.** 운영은 `training_pairs`(`utf8mb4_unicode_ci`)와
  `item_suggestions`(`utf8mb4_0900_ai_ci`)의 collation이 갈려 있어, 한쪽에 명시하지 않으면
  `ERROR 1267`이다. 근거: 등록(`ItemRepository.ensure_exists`의 `ON DUPLICATE KEY UPDATE`)이
  "이미 있음"을 판정하는 기준이 `item_suggestions.item_name`의 유니크 인덱스이므로,
  진단도 같은 기준으로 봐야 등록 쪽에서 별개 항목인 발산이 숨지 않는다.
  PAD SPACE인 `utf8mb4_unicode_ci`로 비교하면 사전에 `'휠 '`(뒤 공백)만 있고 `'휠'`은 없는
  상태가 0행으로 나온다(실측) — `POST/PUT /api/items`는 `item_name`을 strip하지 않으므로
  도달 가능한 상태다.
- **정규화 조건을 빼거나 `TRIM()`으로 바꾸지 않는다.** 등록 쪽(`CurationService._register_label`)은
  Python `.strip()`으로 정규화한 뒤 빈 값을 건너뛴다. MySQL `TRIM()`은 ASCII 스페이스만 지우므로
  탭·U+3000이 붙은 라벨에서 등록과 진단이 갈리고, 정상 등록된 라벨이 영구히 발산으로 보고된다.
  `REGEXP_REPLACE(…, '^[[:space:]]+|[[:space:]]+$', '')`가 그 두 규칙을 일치시킨다.
  정규식에 `\s`를 쓰지 않는다 — MySQL 리터럴에서 백슬래시가 소비돼 알파벳 `s`를 지운다.
- 진단 조건은 등록 조건(`CurationService`·`db/migration_010_sync_item_vocabulary.sql`)과
  **정확히 같아야 한다**. 한쪽만 고치면 0행 불변식이 조용히 깨진다.
- 이 SQL은 `apps/invoice-ocr/backend/tests/integration/test_item_vocabulary_diagnostic_sql.py`가
  이 문서에서 직접 읽어 실행한다 — 여기를 고치면 그 테스트가 함께 반응한다.
  읽는 대상은 앵커 `<!-- diagnostic-sql -->` 바로 다음 sql 펜스이므로, 앵커를 지우거나
  진단 SQL과 떼어놓지 않는다(다른 예시 SQL을 이 절에 추가하는 것은 안전하다).

`increment_usage_by_name`의 0행 갱신(청구 이름이 사전에 없는 경우)은 이 신호와 다르다.
실측상 `invoice_items`의 37%가 정상적으로 사전에 없는 이름이라 그 무음은 경보로 쓸 수 없다.
청구 이름은 자유 텍스트이고 사전 부재는 오류가 아니다(ADR 0008).

## 개선 작업으로 잇기

| 발견                           | 다음 작업                                                                                                                                            |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| out_of_bank 누적               | 뱅크 증분 갱신 — `docs/runbooks/ocr-bank-update.md`                                                                                                  |
| warp_suspect 잡                | `warped.png` 육안 확인 → 게이트(`handwriting/warp_gate.py`)가 이미 운영 중이므로, 판정이 실물과 어긋나면 `tools.warp_gate_report`로 지표·마진 재산출 |
| row_gap 잡(행 수지 이상)       | 원본 사진과 행검출 결과 대조 — `pull-images --jobs <id> --originals`로 원본을 받아 모델이 놓친 행/버린 행의 위치를 확인한다                          |
| in_bank_miss 크롭 잘림         | `_crop_diagnose_viz.py`로 경계 재검증 (우측 확장은 ADR 0005 참조)                                                                                    |
| degenerate 반복                | 재시도(`amount_read.read_amount_with_retry`)를 통과한 잔여이므로 `amount_raw`의 시도별 원문으로 프롬프트·크롭을 확인                                 |
| unknown/stale_bank 다수        | 재평가 실행 — `docs/runbooks/ocr-bank-update.md` 4단계(`--scope all`)                                                                                |
| 재평가 상태가 stale            | 릴리스 배포 후인지 확인 — 배포는 지문을 바꾼다. `score --scope all` 재실행                                                                           |
| 품목 어휘 발산 진단이 0행 아님 | 진단 절의 원인 표를 위에서부터 확인 — 사람의 사전 편집 / 서비스 밖 writer / 배포 컷오버 / 등록 경로 회귀 (ADR 0008, #40)                             |

분석 결과와 개선 결정은 `docs/work/{yyyy-mm}/{yyyy-mm-dd}-{job-slug}/`에 기록한다.
첫 분석(2026-07-27, 잡 15개·46쌍 기준 top-1 26%)의 상세와 개선 우선순위는
`docs/work/2026-07/2026-07-27-ocr-curation-analysis/analysis.md`(로컬 전용) 참조.

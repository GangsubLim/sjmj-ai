# OCR 큐레이션 데이터 분석 루프

배포 서버(macmini)에 쌓이는 학습 큐레이션 데이터(training_pairs + 크롭 이미지)를
주기적으로 분석해 OCR 정확도 개선 방향을 도출하는 절차.
사용자가 명시적으로 지시할 때 AI 에이전트(Claude Code)가 수행한다.

도구: `apps/invoice-ocr/ml/tools/curation_report.py` (stdlib 전용, ssh로 서버 접근).
원격 접속값은 env 주입 — 기본값은 현행 배포 관례(`ml/.env.example` 참조).

## 절차

모든 명령은 `apps/invoice-ocr/ml`에서 실행한다.

```bash
# 0. (선택·권장) macmini에서 재평가 산출 — 현재 뱅크로 다시 retrieval해 지표를 복원한다.
#    이것을 돌리지 않으면 스탬프 이전 잡은 전부 판정 불가(unevaluable)로 나온다.
#    절차는 docs/runbooks/ocr-bank-update.md 4단계(--scope all) 참조.

# 1. 서버에서 최신 데이터 동기화 (training_pairs + result_json + 뱅크 라벨 + 현재 지문 + 재평가)
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

> [!NOTE]
> **이 기능(era-aware 재평가, Issue #49)의 최초 배포 전 한정** — 배포되기 전에는 `fetch`가
> 서버의 `handwriting.bank_id`를 import하지 못해 `ImportError`로 실패하고(서버에 `handwriting`
> 패키지는 이미 있고 `bank_id`만 없으므로 `ModuleNotFoundError`가 아니다), macmini
> `score --scope all`도 같은 이유로 실행할 수 없다. 배포 순서·근거는
> `docs/runbooks/ocr-bank-update.md` 4단계 WARNING 참조. **배포가 끝나면 이 알림은 소거
> 대상이다.**

> [!WARNING]
> **릴리스 배포는 재평가를 무효화한다.** retrieval 지문에는 배포 코드 SHA가 들어간다(전처리·
> 임베딩·후보 선택 코드가 결과 경로의 일부이고 `deploy.yml`이 배포마다 워커를 재시작한다).
> 따라서 프론트·문서만 바뀐 릴리스여도 지문이 바뀌어 과거 재평가는 `stale`로 기각되고 과거
> 잡은 `stale_bank`가 된다 — **배포 후에는 `bank_update score --scope all`을 다시 돌린다.**
> 이것은 버그가 아니라 정직한 표현이다(코드가 바뀌면 추론 경로가 바뀐다).

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
   - `excluded` — 검수자가 학습에서 뺀 쌍(해석 비대상).

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
   - `row_missing`: 학습쌍의 crop_ref가 현재 result_json에 없음(재처리 등) — 모델 문제가
     아니라 데이터 정합 문제이므로 성능 해석에서 제외하고 원인을 별도 확인한다.
   - `unevaluable`: 위 0번 코호트가 판정 불가로 격리한 쌍 — 시점 정합이 안 돼 지금 채점할
     근거가 없다는 뜻이며, `row_missing`(데이터 정합 문제)과는 관심사가 다르다.
3. **금액 버킷**:
   - `zero_drift`(0으로 읽음)가 잡 단위로 몰리면 `warp_suspect` 플래그가 붙는다 —
     해당 잡 `warped.png`를 반드시 눈으로 확인. 쿼드 오검출(배경 포함·오프셋)이면
     템플릿 좌표(ITEM_X/AMOUNT_X)가 통째로 어긋난 것.
   - `degenerate`(`!!!` 등): MLX-VLM 퇴화 출력 — 재시도 로직 부재가 원인.
   - `misread`: 행 밴드와 손글씨 세로 어긋남(오프바이원) 또는 자릿수 오독 — warp와
     인접 행 금액을 같이 봐야 구분된다.
4. **excluded** 쌍은 검수자가 학습에서 뺀 것으로, 두 종류를 구분해서 읽는다.
   - **크롭 불량** — 빈 품목칸 행 검출 등 행검출 이슈의 직접 신호. 개선 대상이다.
   - **원본에 품목 미기재** — 이미지에 정답이 없는 행(아래 "검수 시 제외 기준").
     개선 신호가 아니므로 성능 해석에서 제외한다.

## 검수 시 제외 기준

큐레이션 검수 화면에서 다음 행은 `excluded`로 내린다.

- **원본에 품목명이 없는 행** — 타이어처럼 관례상 품목명을 생략하고 `단가 × 수량`만
  적는 전표가 있다. 검수자가 맥락으로 정답을 채워 넣어도 이미지에는 근거가 없어
  학습쌍이 성립하지 않는다. 모델은 이런 행에서 품목칸 대신 수량·단가 숫자를 크롭하므로,
  included로 두면 리트리벌 실패로 집계되어 지표를 왜곡한다(2026-07-29 잡 53).
- **크롭 불량** — 행 오프셋·옆칸 침입 등으로 손글씨가 온전히 담기지 않은 행.

자동 판별은 하지 않는다 — 잡 53은 숫자를 잘못 크롭했는데도 top-1 유사도가 0.862로
적중 평균(0.845)을 웃돌아, 미확신 신호로 걸러지지 않는다.

## 개선 작업으로 잇기

| 발견                    | 다음 작업                                                                  |
| ----------------------- | -------------------------------------------------------------------------- |
| out_of_bank 누적        | 뱅크 증분 갱신 — `docs/runbooks/ocr-bank-update.md`                        |
| warp_suspect 잡         | rectify.form_quad_robust 실패 사례로 등록, warp 검증 게이트 설계           |
| in_bank_miss 크롭 잘림  | `_crop_diagnose_viz.py`로 경계 재검증 (우측 확장은 ADR 0005 참조)          |
| degenerate 반복         | read_amount에 퇴화 감지 + 재시도 추가                                      |
| unknown/stale_bank 다수 | 재평가 실행 — `docs/runbooks/ocr-bank-update.md` 4단계(`--scope all`)      |
| 재평가 상태가 stale     | 릴리스 배포 후인지 확인 — 배포는 지문을 바꾼다. `score --scope all` 재실행 |

분석 결과와 개선 결정은 `docs/work/{yyyy-mm}/{yyyy-mm-dd}-{job-slug}/`에 기록한다.
첫 분석(2026-07-27, 잡 15개·46쌍 기준 top-1 26%)의 상세와 개선 우선순위는
`docs/work/2026-07/2026-07-27-ocr-curation-analysis/analysis.md`(로컬 전용) 참조.

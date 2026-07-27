# OCR 큐레이션 데이터 분석 루프

배포 서버(macmini)에 쌓이는 학습 큐레이션 데이터(training_pairs + 크롭 이미지)를
주기적으로 분석해 OCR 정확도 개선 방향을 도출하는 절차.
사용자가 명시적으로 지시할 때 AI 에이전트(Claude Code)가 수행한다.

도구: `apps/invoice-ocr/ml/tools/curation_report.py` (stdlib 전용, ssh로 서버 접근).
원격 접속값은 env 주입 — 기본값은 현행 배포 관례(`ml/.env.example` 참조).

## 절차

모든 명령은 `apps/invoice-ocr/ml`에서 실행한다.

```bash
# 1. 서버에서 최신 데이터 동기화 (training_pairs + result_json + 뱅크 라벨)
uv run python -m tools.curation_report fetch

# 2. 분석 리포트 생성 → results/curation/report.md + failures.jsonl
uv run python -m tools.curation_report report

# 3. (리포트의 실패 건 검수 시) 실패 잡의 크롭·warp 이미지 동기화
uv run python -m tools.curation_report pull-images            # 실패 잡 전체
uv run python -m tools.curation_report pull-images --jobs 39 44 --originals
```

## 리포트 읽는 법 (에이전트 체크리스트)

1. **핵심 지표** — 이전 리포트와 비교해 top-1/top-5/금액 일치 추이를 확인한다
   (리포트는 `results/curation/`에 덮어써지므로 추이 비교가 필요하면 이전 값을 기록해 둘 것).
2. **라벨 버킷** 우선순위:
   - `out_of_bank`: 뱅크에 정답이 없어 구조적으로 못 맞춘 것. **뱅크 추가가 유일한 해결책**
     — 해당 크롭을 `pull-images`로 받아 품질 확인 후 뱅크 갱신 작업으로 잇는다.
   - `top5_only`: 후보엔 있었음 — 뱅크 프로토타입 보강·리랭킹 여지.
   - `in_bank_miss`: 뱅크에 있는데 후보 밖 — 크롭 품질(우측 잘림·옆칸 잉크 침입) 또는
     동일 라벨의 필체 변형 부족을 의심하고 크롭을 눈으로 확인한다.
   - `row_missing`: 학습쌍의 crop_ref가 현재 result_json에 없음(재처리 등) — 모델 문제가
     아니라 데이터 정합 문제이므로 성능 해석에서 제외하고 원인을 별도 확인한다.
3. **금액 버킷**:
   - `zero_drift`(0으로 읽음)가 잡 단위로 몰리면 `warp_suspect` 플래그가 붙는다 —
     해당 잡 `warped.png`를 반드시 눈으로 확인. 쿼드 오검출(배경 포함·오프셋)이면
     템플릿 좌표(ITEM_X/AMOUNT_X)가 통째로 어긋난 것.
   - `degenerate`(`!!!` 등): MLX-VLM 퇴화 출력 — 재시도 로직 부재가 원인.
   - `misread`: 행 밴드와 손글씨 세로 어긋남(오프바이원) 또는 자릿수 오독 — warp와
     인접 행 금액을 같이 봐야 구분된다.
4. **excluded** 쌍은 검수자가 "크롭 불량"이라고 알려준 것 — 빈 품목칸 행 검출 등
   행검출 이슈의 직접 신호다.

## 개선 작업으로 잇기

| 발견                   | 다음 작업                                                         |
| ---------------------- | ----------------------------------------------------------------- |
| out_of_bank 누적       | 검수 완료(included) 크롭으로 뱅크 증분 갱신 (fewshot.py 계열)     |
| warp_suspect 잡        | rectify.form_quad_robust 실패 사례로 등록, warp 검증 게이트 설계  |
| in_bank_miss 크롭 잘림 | `_crop_diagnose_viz.py`로 경계 재검증 (우측 확장은 ADR 0005 참조) |
| degenerate 반복        | read_amount에 퇴화 감지 + 재시도 추가                             |

분석 결과와 개선 결정은 `docs/work/{yyyy-mm}/{yyyy-mm-dd}-{job-slug}/`에 기록한다.
첫 분석(2026-07-27, 잡 15개·46쌍 기준 top-1 26%)의 상세와 개선 우선순위는
`docs/work/2026-07/2026-07-27-ocr-curation-analysis/analysis.md`(로컬 전용) 참조.

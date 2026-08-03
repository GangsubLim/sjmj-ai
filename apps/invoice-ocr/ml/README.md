# invoice-ocr / ml

수기(손글씨) 거래명세서 OCR. SP1 배치 CLI(`ocr_poc/`) + macmini에 배포된 추론 worker(`worker/` + `handwriting/`).
외부 API 0, 전 과정 로컬. 코드 트랙과 상세 규약은 `AGENTS.md` 참조.

## 실행

```bash
cd apps/invoice-ocr/ml
uv sync                  # 코어(경량): pillow + dev pytest
cp .env.example .env     # 경로 채우기
# 환경/검출 스파이크 (paddle 필요 → 먼저 extra 설치)
uv sync --extra ml
uv run python -m tools.spike_ppstructure inv_003
# 본 파이프라인 (Task 13에서 완성, paddle 필요)
uv run python -m ocr_poc match-extract     # reviewed_dates.csv 생성
# (사람이 reviewed_dates.csv 검수)
uv run python -m ocr_poc run               # 38장 배치 → report/
```

데이터·DB는 레포 밖(OneDrive/타 레포). 경로는 `.env`로 주입한다.

## 품목 인식 — 새 품목이 반영되기까지

품목은 글자를 **읽지 않는다**.
crop 이미지를 인코더로 임베딩해 뱅크(`bank.npz`)의 exemplar와 최근접 검색으로 정식명을 retrieval한다(`handwriting/fewshot.py`).
금액은 반대로 VLM이 직접 판독한다(`handwriting/amount_read.py`).

초기 뱅크의 정답 라벨도 OCR 산출이 아니다 — `handwriting/dataset_build.py`가 명시하듯 **모든 라벨(GT)은 DB `invoice_items`가 단일 출처**이고,
사진↔전표 매칭(발행일 + `total_supply` 유일조회)으로 행 크롭에 그 라벨을 붙였다.
그래서 뱅크에 exemplar가 없는 품목은 원리상 제시할 후보가 없다 — 첫 등장은 반드시 사람이 입력한다.

| 단계                 | 무엇이 일어나나                                                                                                | 자동/수동              |
| -------------------- | -------------------------------------------------------------------------------------------------------------- | ---------------------- |
| 1. 첫 등장           | 후보 0개 또는 top1 유사도 임계 미만 → `item_uncertain=True`. 검수 UI가 "추천 후보가 없습니다 — 직접 입력" 안내 | —                      |
| 2. 확정(confirm)     | 사람이 입력한 이름이 `invoice_items` + `training_pairs`(`canonical_label`, `crop_ref=job-N/row-K`)로 저장      | 자동                   |
| 3. 큐레이션 검수완료 | `ocr_jobs.curation_reviewed=1` + 자동완성 사전(`item_suggestions`) 등록 — 같은 트랜잭션(#40)                   | 자동                   |
| 4. 뱅크 갱신         | 검수완료 잡의 included 쌍을 **현재 모델로 재임베딩**해 `bank.npz`에 멱등 sync                                  | **수동**               |
| 5. ml-worker 재시작  | 워커는 기동 시 뱅크를 1회만 적재한다 — 재시작 전까지 파일 변경이 추론에 반영되지 않는다(`worker/main.py`)      | **수동**(배포 시 자동) |
| 6. 다음 전표         | 그 품목이 top-5 후보로 제안된다                                                                                | —                      |

4단계는 macmini에서 직접 실행한다(ADR 0001, 런북 `docs/runbooks/ocr-bank-update.md`).

```bash
uv run python -m tools.bank_update plan
uv run python -m tools.bank_update apply --plan results/bank_update/plan.jsonl
uv run python -m tools.bank_update score --before <bank.npz.bak> --after <bank.npz>
```

`apply`는 백업을 자동 생성하고, 제거가 포함된 plan은 명시 승인 없이 거부한다.
구세대 부트스트랩 key(`2025-08-18_inv011_0` 형식)는 건드리지 않는다.
5단계 재시작 후 `ml-worker.err.log`에 `[retrieval-version] 부팅 지문=...`이 찍힌다 — 그 잡이 어느 retrieval 상태로 추론됐는지의 판정 근거다(`handwriting/bank_id.py`).

> [!WARNING]
> 3단계(사전)는 자동·즉시지만 4~5단계(뱅크)는 수동이다.
> 그 사이 구간에서는 타이핑하면 자동완성에 뜨는데 OCR은 여전히 그 품목을 모른다 — "자동완성엔 있는데 왜 못 잡지?"의 정체다.

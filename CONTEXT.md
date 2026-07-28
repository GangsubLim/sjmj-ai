# sjmj-ai Context

수기 거래명세서 OCR 자동입력 플랫폼. PHP+React 원본(SJMJ-Web)을 Python(FastAPI)+React로 동형 포팅해 macmini에 배포(Phase 1)하고, 검증된 ML PoC를 이음새에 끼워 production 추론 + 피드백 루프로 승격(Phase 2)한다.

## Language

### ML 도메인 (Phase 2)

**품목 인식기 (Item recognizer)**:
사진의 품목칸을 작성자-특화 retrieval로 식별하는 인식기. `ddobokki/ko-trocr` ViT 인코더 + contrastive head(torch). 재학습 대상이며, 뱅크 대조로 top-5 후보를 낸다.
_Avoid_: 그냥 "모델"(어느 모델인지 모호 — 금액 인식기와 구분 필수)

**품목 크롭 (Item crop)**:
품목 인식기에 넣기 전, warp된 전표에서 품목칸 영역을 잘라내는 전처리 단계(`ITEM_X` 고정 x좌표 + 잉크 기반 우측 동적 확장). 학습 가능한 파라미터가 없는 순수 기하학적 처리이며, 재학습(품목 인식기 fine-tune) 대상이 아니다.
_Avoid_: "크롭 엔진을 학습시킨다"(크롭 단계엔 학습 파라미터가 없음 — 재학습은 크롭 이후의 품목 인식기만 해당. 근거: ADR 0005)

**금액 인식기 (Amount recognizer)**:
금액칸 손글씨를 전사하는 VLM. `Qwen3-VL-8B-Instruct-4bit`(MLX). **추론 전용 — 절대 학습하지 않는다.** 어휘 무관이라 신규 전표에도 즉시 작동.
_Avoid_: 그냥 "모델", "VLM 학습"(금액 인식기는 학습 대상이 아님)

**재학습 (Retraining)**:
품목 인식기의 마지막 2개 ViT 층 + head만 fine-tune하고 `bank.npz`를 재생성하는 분 단위 잡(`train_contrastive --production`, 22 epoch). 8B 금액 인식기는 건드리지 않는다.
_Avoid_: "모델 학습"(8B를 학습하는 것으로 오해), "트레이닝"

**뱅크 (Bank)**:
품목 인식기가 retrieval하는 임베딩 사전(`bank.npz`). 교정 crop이 누적될수록 갱신되어 신규→재현 어휘 전환으로 정확도가 오른다.

**매칭키 (Matching key)**:
기존 운영 invoice ↔ 스캔 사진을 사후 짝짓는 **backfill 전용** 키 `invoices.total_supply`(공급가합, VAT 제외). 초기 학습쌍 부트스트랩에만 쓴다. 라이브 HITL 플로우는 매칭키를 안 쓰고 job↔invoice를 직접 연결한다. `grand_total`(VAT 포함)로 바꾸면 조회 0건.
_Avoid_: grand_total, 합계금액

**추론 잡 (OCR job)**:
사진 업로드부터 초안 JSON까지의 비동기 작업 단위. `ocr_jobs` 테이블에 상태(pending/running/done/failed)로 추적.

**초안 (Draft)**:
추론 잡이 낸 미확정 결과 JSON(품목 top-5 + 금액). 사람이 검수·교정하기 전 상태.

**교정 (Correction)**:
사람이 초안을 고친 내용(`ocr_corrections`). 곧 재학습의 정답지가 된다.

**학습 후보 쌍 (Training pair)**:
confirm된 잡의 각 행에서 나온 `(row crop 이미지 → 확정 품목 라벨)` 쌍. 사용자가 라벨을 고쳤는지와 무관하게 확정된 모든 행이 후보다(맞춘 행은 양성 라벨). 품목 인식기 재학습의 단위이며, 금액은 학습 대상이 아니라 후보를 이루지 않는다.
_Avoid_: "수정쌍"(변경된 행만으로 오해), "학습 데이터"(범위 모호)

**큐레이션 (Curation)**:
재학습 직전, 누적된 학습 후보 쌍을 사람이 검토해 배제·수정·승인하는 단계. 학습 후보가 의도대로 정리됐는지 확인해 정답지의 품질을 보장한다. confirm 시점의 1차 검수와 구분되는 **2차 관문**이다.
_Avoid_: "검수"(confirm 시점 1차 검수와 혼동), "완전 자동화"(로드맵 목표 아님 — 사람 개입은 영구 유지, 재학습으로 배제·수정 비율만 낮춘다. 근거: ADR 0005)

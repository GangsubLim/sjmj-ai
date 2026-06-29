# ML 추론은 단일 프로세스 device-분리 워커로 서빙한다

Phase 2 ML 추론은 품목 인식기(torch)와 금액 인식기(MLX)를 **한 프로세스 안에서 device 분리**(품목=CPU, 금액=MLX/Metal)로 돌리는 단일 워커로 서빙한다. 이 워커는 web backend(`ai.sjmj.backend` :8400)와 별개인 전용 launchd 잡으로 띄워 `ocr_jobs`를 폴링하고 초안을 쓴다. launchd가 KeepAlive로 supervisor 역할을 하므로 커스텀 프로세스 supervisor는 두지 않는다. 총 launchd 잡은 backend + ml-worker 2개.

이유: §8 torch-MPS↔MLX 공존 불가 제약은 **프로세스 분리가 아니라 device 분리로 이미 해소**되며, 그것이 검증된 토폴로지다(`report/sp2_spike/item/infer_photo.py:main` — `device="cpu"`로 품목 인코더 고정, crop 소수라 CPU forward ~1.6s). HITL 초안은 사람이 어차피 검수하므로 지연이 critical하지 않다. 따라서 로드맵 §5/§8의 "프로세스 분리 + 프로세스 supervisor 도입 = 인프라 재작업" framing은 과하다.

## Considered Options

- **프로세스 분리(품목/금액 각 서비스 + supervisor)** — 품목을 별 프로세스로 빼 MPS 가속이 가능하나 launchd 잡 분리 + IPC + supervisor를 동반. 품목 CPU forward가 production 처리량에 부족할 때만 정당화되는 인프라 재작업. 그 시점에 도입.
- **추론을 backend 프로세스 안에서** — 잡 개수는 최소지만 8B MLX 모델 수명이 web 티어에 묶여 배포 재시작마다 8B 재로드(느림)되고 web 티어 메모리에 상주. 결합도 높음.

## Consequences

- 품목 CPU forward 지연이 production 처리량의 병목이 되면(대량 업로드 등) 그때 프로세스 분리로 승격한다. 그 전까지는 KISS 단일 프로세스 유지.
- ml-worker 잡과 backend 잡은 독립 재시작 가능 — 배포 시 web 티어를 재시작해도 8B는 재로드되지 않는다.

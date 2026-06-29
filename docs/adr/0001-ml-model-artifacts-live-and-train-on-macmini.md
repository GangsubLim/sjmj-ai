# ML 모델 아티팩트는 macmini에 살고 macmini에서 학습한다

Phase 2의 모델 아티팩트(`ft_prod.pt` 331MiB · `bank.npz`)는 macmini의 고정 경로(env 주입, 예: `SJMJ_ML_MODELS_DIR`)에만 두고, CI 배포(`deploy.yml`)는 모델을 전혀 실어 나르지 않는다. 재학습(`train_contrastive --production`)도 macmini에서 직접 돌려 그 경로를 갱신한다. 최초 1회만 현 spike의 `ft_prod.pt`/`bank.npz`를 수동 복사한다.

이유: 재학습 잡이 가볍다(품목 인식기의 마지막 2개 ViT 층 + head만 fine-tune + 뱅크 재생성, 분 단위). 8B 금액 인식기는 추론 전용이라 학습하지 않는다. 따라서 M4 Pro 64GB로 충분하고, 더 강력한 dev 머신(M5 Max 128GB)으로 학습을 빼서 얻는 속도 이득보다 모델 transport + 크로스박스 자동화 복잡도가 더 크다. 모델의 "집"이 곧 재학습이 도는 곳(macmini)이라 CI가 모델을 실어 나르는 건 구조적 중복.

## Considered Options

- **Git LFS 버전관리 + deploy pull** — macmini에서 생성된 모델을 다시 git으로 올렸다 pull하는 왕복이 생긴다(재학습이 macmini에서 도는데).
- **오브젝트 스토리지/NAS + deploy pull** — 다중 호스트 확장 시 유리하나 현 단일 macmini·Tailscale 내부전용 구조엔 인프라 과잉.

## Consequences

- spike 코드를 `report/` 밖 production 경로로 승격할 때, gitignore를 디렉터리-blanket(`report/`)에서 file-precise(`*.pt`/`*.npz`/`runs/`/dataset 디렉터리)로 전환해야 331MiB 모델이 git에 딸려 들어가지 않는다(Phase 2 착수 전 블로커).
- 서빙 경합(라이브 MLX 금액 추론 중 학습 torch 동시 실행)은 재학습이 교정 임계 트리거라 상시가 아니고, 학습을 CPU로 돌리거나 야간 스케줄로 완화한다.

# sjmj-ai

SJMJ 업무 AI 자동화 플랫폼. 첫 모듈은 수기 거래명세서 OCR 자동입력(`apps/invoice-ocr`).

## 진행 단계

원본 SJMJ-Web(PHP+React)을 기능·UI 동등하게 Python(FastAPI)+React로 리팩토링해 macmini에 배포(Phase 1) → 검증된 ML PoC 연결(Phase 2) → 운영 굳히기(Phase 3).

- ✅ **토대** — SP0 인프라 셸 + SP1 OCR PoC (`apps/invoice-ocr/ml`)
- ✅ **Phase 1** 운영 앱 리팩토링 & macmini 배포 — 1A DB 이전 → 1B 백엔드 API 포팅(계약 1:1) → 1C 프론트 이식 → 1D macmini 통합배포. tag→macmini CD 구축 완료
- ⬜ **Phase 2** ML 연결 ← 다음 — PoC를 Phase 1이 남긴 이음새에 끼워 production 서비스 + 피드백 루프
- ⬜ **Phase 3** 운영 굳히기 — 모니터링 · Tailscale 외부 노출

## 로컬 개발

```bash
make install   # backend(uv) + frontend(npm) 의존성
make dev       # FastAPI(:8400) + Vite(:5173) 동시 기동
make build     # 프론트 빌드 → frontend/dist (FastAPI가 서빙)
make test      # 백엔드 pytest
```

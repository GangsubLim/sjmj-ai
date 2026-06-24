# sjmj-ai

SJMJ 업무 AI 자동화 플랫폼. 첫 모듈은 수기 거래명세서 OCR 자동입력(`apps/invoice-ocr`).

## 서브프로젝트 (빌드 순서)

- **SP0** 모노레포 스캐폴딩 + 최소 인프라 골격 ← 현재
- SP1 OCR PoC (검출→인식→산술검산)
- SP2 백엔드 Python 재작성 + DB 이전
- SP3 프론트 이식 (SJMJ-Web frontend)
- SP4 검수 UI + 데이터 축적 루프
- SP5 운영 굳히기 (정식 배포 CI · MySQL 백업 · Tailscale)

설계·근거 문서는 `docs/superpowers/specs/` 참고.

## 로컬 개발

```bash
make install   # backend(uv) + frontend(npm) 의존성
make dev       # FastAPI(:8400) + Vite(:5173) 동시 기동
make build     # 프론트 빌드 → frontend/dist (FastAPI가 서빙)
make test      # 백엔드 pytest
```

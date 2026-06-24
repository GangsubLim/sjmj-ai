> **사본 안내** — 원본 정본: `GangsubLim/SJMJ-Web` → `docs/superpowers/specs/2026-06-24-sjmj-ai-sp0-scaffold-design.md`
> (로컬 `/Users/gangsub/projects/SJMJ-Web/docs/superpowers/specs/2026-06-24-sjmj-ai-sp0-scaffold-design.md`). 정본 수정은 SJMJ-Web 레포에서. 이 파일은 sjmj-ai로 복사된 사본입니다.

# sjmj-ai SP0 — 모노레포 스캐폴딩 + 최소 인프라 골격 (설계 spec)

- 작성일: 2026-06-24
- 상위 프로젝트: `sjmj-ai` (SJMJ 업무 AI 자동화 플랫폼, invoice OCR이 첫 모듈)
- 관련: [`2026-06-24-macmini-migration-overview.md`](./2026-06-24-macmini-migration-overview.md), [`2026-06-24-invoice-htr-tech-review.md`](./2026-06-24-invoice-htr-tech-review.md), [`2026-06-24-invoice-handwriting-ocr-poc-design.md`](./2026-06-24-invoice-handwriting-ocr-poc-design.md)
- 성격: **첫 서브프로젝트(SP0)의 구현 설계 문서.** 다음 단계는 writing-plans로 TDD 구현 plan 작성.

---

## 0. 확정된 전제 (이 세션의 5개 결정)

overview §8의 열린 질문을, augron·donboksa macmini 배포 선례 조사를 근거로 확정했다.

| 결정 | 확정 | 근거 |
|------|------|------|
| 백엔드 언어 | **Python(FastAPI) 통합** | donboksa(Python FastAPI + React + macmini + launchd)가 거의 1:1 선례 — 배포 자산 직접 차용 |
| 레포 | **새 레포 + 모노레포** (`~/projects/sjmj-ai`) | 빈 폴더 생성됨. 선례는 정돈된 단일 레포 루트를 macmini 경로에 하드코딩 |
| 우선순위 | **병행** (인프라 최소 + OCR PoC 메인) | 인프라는 선례 차용으로 가볍고, OCR 정확도가 프로젝트 최대 리스크 |
| 접속 | **Tailscale VPN 내부 전용** | augron이 실증한 유일한 외부 HTTPS 경로. 민감정보 on-prem 유지 |
| OCR 범위 | **추론 + 데이터 축적까지** (재학습 수동) | 사진↔DB 매칭으로 학습데이터 자동 축적, 풀 학습 루프는 이후 |

### 서브프로젝트 분해 (빌드 순서)

```
SP0  모노레포 스캐폴딩 + 최소 인프라 골격           ← 본 문서
SP1  OCR PoC (0~2단계)  ★메인 리스크 트랙
SP2  백엔드 Python 재작성 + DB 이전
SP3  프론트 이식 (SJMJ-Web frontend → api.ts URL만 조정)
SP4  검수 UI + 데이터 축적 루프 (사진↔DB 매칭)
SP5  운영 굳히기 (정식 tag 배포 CI·자동롤백·MySQL 백업·Tailscale 외부 e2e)
```

각 SP는 자체 spec → plan → 구현 사이클을 가진다.

---

## 1. SP0 범위

### 포함

- `sjmj-ai/` git 레포 초기화 + 원격(GitHub) 생성·연결(macmini `git clone`의 전제)
- `sjmj-ai/` 모노레포 디렉터리 골격 생성(구조 선언 + SP0 필요분 채움)
- 백엔드 최소 셸: FastAPI `GET /health` + 프론트 정적 산출물(`dist`) 서빙
- 프론트 최소 셸: Vite 빈 SPA(SP3에서 SJMJ-Web 이식)
- 로컬 dev 실행 흐름(`make dev` / `make build`)
- macmini **수동 SSH 배포** + launchd 상시구동(`ai.sjmj.backend`)
- 기존 OCR 근거 문서(overview·htr-review·poc-design) + 본 SP0 문서를 `sjmj-ai/docs/superpowers/specs/`로 **복사**(원본은 SJMJ-Web에 보존). 복사본 상단에 원본 레포 경로 명시(§2 문서 복사 정책)

### 제외 (이후 SP)

- 정식 tag-driven 배포 CI(`deploy-macmini.yml`)·자동 롤백 → **SP5**
- MySQL·DB·운영 데이터 이전 → **SP2**
- MySQL 백업(`mysqldump`)·오프머신 복제·모니터링 → **SP5**
- Tailscale 외부 노출·TLS → **SP5**
- ML 추론(검출/인식/검산) 코드 → **SP1**
- 프론트 실내용·검수 UI → **SP3/SP4**

---

## 2. 디렉터리 구조

```
sjmj-ai/
├── apps/invoice-ocr/
│   ├── backend/
│   │   ├── pyproject.toml          # uv 관리, Python 3.12
│   │   ├── uv.lock
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py             # FastAPI app, /health, StaticFiles 마운트
│   │   │   └── config.py           # env 로딩(PORT, LOG_DIR, STATIC_DIR 등)
│   │   ├── tests/
│   │   │   └── test_health.py
│   │   └── .env.example
│   ├── frontend/                   # Vite 빈 셸 (SP3에서 SJMJ-Web 이식)
│   │   ├── package.json
│   │   ├── vite.config.ts          # server.proxy /api → 127.0.0.1:8400
│   │   ├── index.html
│   │   └── src/main.tsx            # 최소 셸 ("sjmj-ai" placeholder)
│   └── ml/.gitkeep                 # 플레이스홀더 (SP1)
├── worker/.gitkeep                 # 플레이스홀더 (SP4)
├── db/.gitkeep                     # 플레이스홀더 (SP2)
├── packages/.gitkeep               # 플레이스홀더 (공통)
├── deploy/
│   ├── launchd/
│   │   └── ai.sjmj.backend.plist.template
│   └── env/
│       └── backend.env.example
├── scripts/
│   ├── run-backend.sh              # wrapper: env 소싱 + uv run uvicorn (donboksa 차용)
│   └── install-launchagent.sh      # plist 치환 설치 + bootout→bootstrap (donboksa 차용)
├── docs/superpowers/specs/         # overview·htr-review·poc-design·본 문서 복사(원본 경로 명시)
├── Makefile
├── README.md
└── .gitignore
```

빈 플레이스홀더(`ml/`·`worker/`·`db/`·`packages/`)는 `.gitkeep`으로 모노레포 구조만 선언한다. YAGNI: 무거운 빌드 도구(pnpm workspace 등)는 두 번째 모듈이 생길 때 도입.

### 문서 복사 정책

OCR 근거 문서는 **이동이 아니라 복사**한다 — 원본 정본은 SJMJ-Web 레포(`GangsubLim/SJMJ-Web`)에 그대로 남긴다. sjmj-ai로 복사한 각 문서 상단(메타 영역)에 원본 출처를 명시한다:

> 원본 정본: `GangsubLim/SJMJ-Web` → `docs/superpowers/specs/<파일명>` (로컬 `/Users/gangsub/projects/SJMJ-Web/docs/superpowers/specs/<파일명>`)

두 레포에 같은 문서가 존재할 때 어느 쪽이 정본인지 혼란을 막기 위함이다.

---

## 3. 구성요소 설계

### 3.1 백엔드 (FastAPI)

- **의존성 관리**: `uv`(donboksa 차용). `uv sync --frozen`으로 결정론적 설치. `pyproject.toml`에 `fastapi`, `uvicorn[standard]`, dev: `pytest`, `httpx`.
- **`app/main.py`**:
  - `GET /health` → `200 {"status": "ok", "version": "<from pyproject>"}`. (SP5 정식 배포에서 마이그레이션 버전 체크로 확장 예정.)
  - 프로덕션 정적 서빙: `STATIC_DIR`(= `frontend/dist`)이 존재하면 `StaticFiles(html=True)`로 마운트하고, **SPA fallback**(미매칭 GET → `index.html`)을 둔다. `dist`가 없으면(개발 모드) API만 노출.
  - `/api/*` 프리픽스로 API 라우트를 둬, 정적 서빙과 경로 충돌을 막는다. SP0의 health는 `/health`와 `/api/health` 양쪽 노출(로컬 dev 프록시 일관성).
- **`app/config.py`**: env에서 `SJMJ_PORT`(기본 8400), `SJMJ_LOG_DIR`, `SJMJ_STATIC_DIR` 로딩. 시스템 경계 입력 검증(YAGNI 수준: 포트 정수화, 경로 존재 확인).

### 3.2 프론트 서빙 전략 (핵심 설계 선택)

- macmini 프로덕션에서 **FastAPI가 `frontend/dist`를 직접 서빙** → 단일 프로세스·단일 오리진. launchd 서비스가 **1개(backend)**로 끝난다.
- 근거: 우리 프론트는 Vite SPA(정적 빌드)라 donboksa의 Next.js(상시 Node 프로세스 필요)와 달리 별도 상시 서버가 불필요. "인프라 최소"에 부합.
- **대안(채택 안 함)**: 프론트를 별도 정적 서버 launchd 서비스로 분리(donboksa형 2서비스). SP4에서 worker가 추가되고 추론이 API를 블로킹할 우려가 생기면 그때 서비스 분리를 재검토.

### 3.3 로컬 dev 흐름

- `make dev`: Vite dev server(:5173, HMR) + FastAPI(:8400, `uvicorn --reload`) 동시 기동. Vite `server.proxy`로 `/api → http://127.0.0.1:8400`. 개발 중 단일 오리진처럼 동작.
- `make build`: 프론트 `npm run build` → `frontend/dist` 생성(FastAPI가 서빙할 산출물).
- Makefile은 SJMJ-Web의 타깃 네이밍 관습을 따른다(`dev`/`build`/`verify`).

### 3.4 macmini 수동 SSH 배포

- **대상**: `submini@macmini.tail99e9f1.ts.net`, working-dir `/Users/submini/sjmj-ai`.
- **포트**: backend `8400`, `127.0.0.1` 바인딩(외부 노출은 SP5). donboksa 82xx/83xx, augron 3100과 충돌 회피.
- **상태 디렉터리**: `~/.sjmj-ai/`(repo 밖)
  - `backend.env`(chmod 600): `SJMJ_PORT`, `SJMJ_LOG_DIR`, `SJMJ_STATIC_DIR`, `UV_BIN`(절대경로). DB·시크릿은 SP2 이후.
  - `logs/backend.{out,err}.log`: launchd `StandardOutPath`/`StandardErrorPath`.
- **launchd**: `ai.sjmj.backend`
  - `RunAtLoad=true`, `KeepAlive=true`(상시구동·크래시 자동 재기동).
  - 모든 경로 **절대경로**(launchd가 `.zshrc`/`.bashrc` 미로드 — 선례 함정).
- **`scripts/run-backend.sh`**(donboksa 차용): `set -a; source ~/.sjmj-ai/backend.env; set +a` → `"$UV_BIN" run uvicorn app.main:app --host 127.0.0.1 --port "$SJMJ_PORT"`.
- **`scripts/install-launchagent.sh`**(donboksa 차용): `.plist.template`의 `__PLACEHOLDER__` 치환 → `~/Library/LaunchAgents/ai.sjmj.backend.plist` 생성 → `bootout → 폴링 대기(1~15s) → bootstrap(재시도) → kickstart -k` 순서로 적용. bash 3.2 호환(`mapfile` 금지, while-read), bootout 비동기 소켓 레이스 폴링 회피 패턴 보존.
- **수동 절차**:
  ```bash
  ssh submini@macmini.tail99e9f1.ts.net
  git clone <repo-url> /Users/submini/sjmj-ai && cd /Users/submini/sjmj-ai
  cd apps/invoice-ocr/backend && uv sync --frozen
  cd ../frontend && npm ci && npm run build
  cd /Users/submini/sjmj-ai && scripts/install-launchagent.sh
  curl -fsS http://127.0.0.1:8400/health      # 200 확인
  ```

---

## 4. 데이터 흐름 / 실행

```
[개발 맥북]
  make dev   → Vite(:5173) ─proxy /api→ FastAPI(:8400)         (HMR 개발)
  make build → frontend/dist 생성

[macmini]
  launchd(ai.sjmj.backend) → run-backend.sh → uvicorn(:8400, 127.0.0.1)
    └ FastAPI: /health 200  +  / → frontend/dist (SPA fallback)
  재부팅/크래시 → KeepAlive 자동 재기동
```

SP0에는 DB·외부 의존이 없다(상태 없는 셸). 따라서 데이터 흐름은 정적 서빙 + 헬스체크로 한정된다.

---

## 5. 에러 처리

- **정적 산출물 부재**: `STATIC_DIR`이 없으면(개발/빌드 전) 정적 마운트를 건너뛰고 API만 노출. 크래시 금지.
- **포트 점유**: uvicorn 기동 실패 시 launchd가 재시도(KeepAlive) — 로그(`backend.err.log`)에 원인 기록.
- **env 누락**: `config.py`에서 필수 env 부재 시 기본값 사용(SJMJ_PORT=8400 등) + 경고 로그. 시크릿류는 SP0에 없음.
- **install 스크립트 실패**: bootout/bootstrap 레이스는 폴링 재시도로 흡수. 최종 실패 시 비정상 종료 코드 + stderr 메시지.

---

## 6. 테스트 & 검증

SP0는 비즈니스 로직이 거의 없으므로(스캐폴딩·인프라) 스모크 수준으로 검증한다. 본격 테스트(80% 커버리지)는 SP2(백엔드 로직)부터 적용.

**자동 테스트**
- `pytest tests/test_health.py`: `httpx`로 `GET /health` → 200 + `{"status":"ok"}` 검증.
- (선택) `STATIC_DIR` 마운트 분기 테스트: dist 존재/부재 시 동작.

**수동 검증(성공 기준)**
1. `make dev` → 로컬 Vite(:5173) + FastAPI(:8400) 동시 기동, `/api/health` 프록시 200.
2. `make build` → FastAPI가 `dist` 서빙: `/`에서 프론트 빈 셸, `/health` 200.
3. macmini 수동 배포 → `curl http://127.0.0.1:8400/health` 200.
4. macmini에서 `launchctl kickstart -k`(또는 프로세스 kill) 후 **자동 재기동** 확인(KeepAlive).
5. macmini 재부팅 후 `/health` 200(RunAtLoad).

---

## 7. 이후 SP로의 연결점

- **SP1(PoC)**: `apps/invoice-ocr/ml/`에 추론 코드. macmini uv 환경이 SP0에서 이미 구성됨 → 바로 착수 가능.
- **SP2(백엔드/DB)**: `app/`에 라우트·서비스·ORM 추가, `db/`에 스키마·Alembic, MySQL 연동. `/health`에 마이그레이션 버전 체크 추가.
- **SP3(프론트)**: `frontend/`에 SJMJ-Web 이식, `api.ts` URL을 `/api`로 조정. SP0의 Vite proxy·정적 서빙이 그대로 수용.
- **SP4(검수/축적)**: `worker/`에 비동기 큐. 추론이 API를 블로킹하면 이 시점에 백엔드/worker 서비스 분리 재검토.
- **SP5(운영)**: `deploy/`에 `deploy-macmini.yml`(tag-driven, self-hosted runner) + 자동 롤백 + `mysqldump` 백업 + Tailscale.

---

## 8. 미해결/가정

- macmini SSH 접속(`submini@macmini.tail99e9f1.ts.net`)이 이 맥북에서 가능하다고 가정(선례 조사 기반). 구현 시 최초 1회 접속 확인 필요.
- macmini Python(uv)·Node(npm) 가용성 가정(augron/donboksa가 이미 사용 중이므로 설치돼 있을 가능성 높음). 부재 시 SP0 구현 중 설치.
- 레포 원격(GitHub) 생성·연결은 SP0 구현 첫 단계에서 처리(현재 `~/projects/sjmj-ai`는 빈 로컬 폴더).

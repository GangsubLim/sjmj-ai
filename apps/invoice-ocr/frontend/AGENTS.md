# invoice-ocr / frontend

React 19 + Vite + Tailwind v4 + shadcn. modern API(구조화 envelope)에 붙는 SPA다.

> 백엔드 연동·환경변수의 전체 맥락은 레포 루트 `AGENTS.md`("프론트엔드" 섹션)에 있다. 이 문서는 이 디렉터리에서 작업할 때 바로 쓰는 운영 정보만 담는다.

## 디렉터리 지도

```
src/
  main.tsx         엔트리
  app/             라우트 단위 페이지(list / edit / companies / items / settings / sales-performance)
  components/      도메인별(invoice / company / item / sales-performance / settings / layout) + ui/(shadcn, 34개)
  hooks/           use-*.ts (데이터 페칭·UI). *.test.ts 코로케이트
  services/api.ts  axios 클라이언트. API_MODE/USE_MOCK/BASE_URL 분기의 단일 진입점
  stores/          zustand(settings-store 등)
  types/           도메인 타입(invoice/company/item/sales-record/salesperson/settings/ocr/api)
  utils/           calculations / calendar / formatters / validators / clipboard (+ *.test.ts)
  mocks/           VITE_USE_MOCK=true일 때 쓰는 가짜 API 데이터
  lib/utils.ts     cn() 등 shadcn 유틸
  styles/          globals.css + invoice-document.css
tests/e2e/         playwright (라이브 백엔드 필요)
```

## 명령어

```bash
npm run dev            # Vite(:5173). /api 는 vite proxy로 :8400 FastAPI에 붙음
npm run build          # tsc -b && vite build → dist
npm run lint           # eslint (CI 게이트)
npm run format:check   # prettier (CI 게이트)
npm run test           # vitest run (단위)
npm run test:coverage  # vitest --coverage
npm run test:e2e       # playwright (라이브 백엔드 필요)
```

## 컨벤션 / 함정 (이 디렉터리 특수)

- **API 동작은 env로 제어** (`.env.example` 복사):
  - `VITE_API_URL`(`/api`) · `VITE_API_MODE`(`modern` — 기본은 코드상 `legacy`이므로 .env에서 반드시 `modern` 지정) · `VITE_USE_MOCK`(`false`).
  - dev는 vite proxy(`/api`→`:8400`), prod는 backend가 `dist`+`/api`를 동일출처로 서빙한다.
- **`services/api.ts`가 API 분기의 단일 진입점.** `API_MODE === "legacy"`면 `/{resource}.php`, `modern`이면 `/{resource}`. Export 등 일부 기능은 modern 전용. 엔드포인트/모드 분기는 여기서만 다룬다.
- **단위 테스트는 대상과 같은 폴더에 코로케이트**(`*.test.ts`). e2e만 `tests/e2e/`에 분리되며 라이브 백엔드를 요구한다.
- **라우트 = `src/app/` 하위 디렉터리.** 새 화면은 해당 패턴을 따른다.
- **shadcn 컴포넌트는 `components/ui/`** 에 둔다(`components.json` 설정). 도메인 컴포넌트와 섞지 않는다.

---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.js"
  - "**/*.jsx"
  - "**/*.mjs"
  - "**/*.cjs"
---

# JS / TS / TSX — NXN Style Guide

[Google JavaScript Style Guide](https://google.github.io/styleguide/jsguide.html)와 [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html)를 기반으로 한 NXN convention. 도구가 자동 강제할 수 있는 부분은 Prettier + ESLint가 처리하며, 이 문서는 **사람이 읽고 따라야 할 서술적 컨벤션**만 다룬다.

## 명명 규칙

| 대상                           | 스타일                                           | 예시                              |
| ------------------------------ | ------------------------------------------------ | --------------------------------- |
| 변수, 함수, 메서드             | `camelCase`                                      | `userName`, `fetchProfile()`      |
| 클래스, 타입, 인터페이스, enum | `PascalCase`                                     | `UserProfile`, `RequestConfig`    |
| 상수 (모듈 레벨, 진짜 불변)    | `UPPER_SNAKE_CASE`                               | `MAX_RETRIES`                     |
| 파일명                         | `kebab-case.ts` 또는 `PascalCase.tsx` (컴포넌트) | `user-service.ts`, `UserCard.tsx` |
| 사적 (private) 멤버            | 선행 `_` 또는 TS `private`                       | `_cache`, `private cache`         |
| Boolean                        | `is`, `has`, `should`, `can` 접두사              | `isReady`, `hasError`             |

**주의**: 약어는 한 단어로 취급 → `parseHttpUrl` (○), `parseHTTPUrl` (×).

## Immutability

객체/배열을 직접 mutate하지 않고 새 인스턴스를 반환한다. 함수가 인자를 몰래 바꾸지 않는다는 보장을 코드 차원에서 제공해 디버깅과 상태 추적이 쉬워지고, React 등 reference identity로 변경을 감지하는 라이브러리와 자연스럽게 맞물린다.

```ts
interface User {
  id: string;
  name: string;
}

// ✗ mutate
function rename(user: User, name: string): User {
  user.name = name;
  return user;
}

// ○ spread로 새 객체
function rename(user: Readonly<User>, name: string): User {
  return { ...user, name };
}
```

- 함수 인자는 가능한 한 `Readonly<T>` / `ReadonlyArray<T>`로 받아 호출자에게 mutate하지 않을 것임을 타입으로 약속.
- 배열 변경: `push`/`splice` 대신 `[...arr, x]`, `arr.filter(...)`, `arr.toSorted()` 등.
- React state, Redux, Zustand 등 상태 라이브러리는 모두 immutable update 전제.

## 변수 선언

- `const` 우선, 재할당이 필요할 때만 `let`. **`var` 금지**.
- 한 줄에 한 변수만 선언. 한 줄에 여러 개는 가독성을 해친다.

```ts
// ✗ 피하기
let a = 1,
  b = 2;

// ○ 권장
let a = 1;
let b = 2;
```

## 비교

- `==` 금지, **항상 `===`** (ESLint `eqeqeq`로 강제됨).
- `null`과 `undefined`를 함께 비교할 때만 `== null` 허용 (ESLint `null: "ignore"`).

## 함수

- **함수 표현식 + 화살표 함수**를 선호. `function` 선언은 모듈 최상위 export에만.
- 인자 4개 이상이면 객체로 분해해서 받는다 (named parameters).

```ts
// ✗
function createUser(name, email, role, locale, timezone) {}

// ○
function createUser(opts: {
  name: string;
  email: string;
  role: Role;
  locale: string;
  timezone: string;
}) {}
```

- 화살표 함수가 한 줄이면 중괄호와 `return` 생략, 여러 줄이면 명시적으로.

## 모듈 / Import

- ES 모듈 (`import` / `export`). CommonJS는 마이그레이션 코드에만.
- import 순서 (ESLint plugin-import 또는 IDE의 자동 정렬 사용 권장):
  1. Node 빌트인 (`node:fs`)
  2. 외부 패키지 (`react`, `lodash`)
  3. 내부 절대경로 별칭 (`@/lib/foo`)
  4. 상대경로 (`./bar`)
- **`import * as X`는 type-only일 때만**. 런타임 코드는 명시적 named import.

```ts
import { useState } from "react";
import type * as Types from "./types"; // ○ type-only
```

## TypeScript 특화

- **`any` 금지** — 타입을 모르겠으면 `unknown` 후 narrow.
- **타입 단언(`as T`)은 최후 수단** — 가능하면 타입 가드 함수로 narrow.
- `interface` vs `type`: 객체 모양은 `interface`, 유니온/매핑/조건부는 `type`. 일관성 있게 쓰면 됨.
- enum 대신 **유니온 리터럴 + `as const`**를 선호 (트리쉐이킹/번들 크기 이점):

```ts
// ✗
enum Role {
  Admin,
  User,
}

// ○
const ROLE = { Admin: "admin", User: "user" } as const;
type Role = (typeof ROLE)[keyof typeof ROLE];
```

## React (TSX)

- 컴포넌트는 함수형 + Hook. 클래스 컴포넌트 금지 (legacy 아니면).
- **`React.FC` / `React.FunctionComponent` 사용 금지** — children을 묵시적으로 포함시켜 props 시그니처가 불명확해지고, 제네릭 컴포넌트 표현이 어색하다. 일반 함수 선언으로 props를 명시한다:

```tsx
// ✗
const UserCard: React.FC<UserCardProps> = ({ user }) => { ... };

// ○
function UserCard({ user }: UserCardProps) { ... }
```

- props는 인라인 타입 또는 별도 `interface XxxProps`. 한 파일 내 일관성.
- 조건부 렌더링: `&&` 대신 `Boolean()` 또는 명시적 ternary로 falsy 함정 회피:

```tsx
// ✗ items.length가 0이면 "0"이 렌더됨
{
  items.length && <List items={items} />;
}

// ○
{
  items.length > 0 && <List items={items} />;
}
```

- `useEffect` deps 배열 누락 금지. ESLint `react-hooks/exhaustive-deps` 룰을 활성화하는 것을 권장 (이 스킬 기본 config에는 미포함 — 프로젝트별 추가).

## 비동기

- **`async/await` 우선**, `.then()` 체인은 합성이 더 읽힐 때만.
- Promise를 던지고 잊지 않는다 (no floating promises). 의도적으로 fire-and-forget 시 `void promise;`로 명시.

## 에러

- `throw new Error("...")` (문자열을 직접 throw 하지 않는다).
- 에러 메시지는 사용자 표시용이 아니라 **개발자가 디버깅**할 수 있도록 명확하게.
- 도메인 에러는 클래스로 분리 (`class ValidationError extends Error`).

## 주석

- **WHAT 대신 WHY**. 코드가 무엇을 하는지는 코드가 말한다. 왜 이렇게 했는지를 적는다.
- TODO는 `// TODO(name): description` 형식 — 책임자 명시.
- 공개 API는 JSDoc/TSDoc, 내부 유틸은 1-2줄 주석으로 충분.

## 파일 구조

- 한 파일 한 export(default) 원칙은 **강제하지 않음**. 응집도 높은 작은 모듈은 함께 두는 게 낫다.
- 파일이 400줄을 넘으면 **분리 신호**. 800줄 이상은 거의 항상 분리해야 한다.

## 참고

- Google JS Style Guide: https://google.github.io/styleguide/jsguide.html
- Google TS Style Guide: https://google.github.io/styleguide/tsguide.html
- ESLint 자동 강제 룰: `assets/configs/eslint.config.js`

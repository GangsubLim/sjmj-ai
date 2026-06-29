---
paths:
  - "**/*.py"
  - "**/*.pyi"
---

# Python — NXN Style Guide

[Google Python Style Guide (pyguide)](https://google.github.io/styleguide/pyguide.html)를 기반으로 한 NXN convention. 도구 자동 강제 영역은 Ruff(format + lint, google docstring convention)가 처리하며, 이 문서는 사람이 읽고 따라야 할 컨벤션만 다룬다.

## 명명 규칙

| 대상               | 스타일                                    | 예시                             |
| ------------------ | ----------------------------------------- | -------------------------------- |
| 모듈, 패키지       | `snake_case`                              | `user_service.py`                |
| 함수, 변수, 메서드 | `snake_case`                              | `fetch_profile()`                |
| 클래스, 예외       | `PascalCase`                              | `UserProfile`, `ValidationError` |
| 상수               | `UPPER_SNAKE_CASE`                        | `MAX_RETRIES`                    |
| 타입 변수          | `PascalCase` (단문자 또는 의미 있는 이름) | `T`, `KeyType`                   |
| 사적(private)      | 선행 `_`                                  | `_internal_cache`                |

Ruff `N` (pep8-naming) 룰로 자동 검증.

## Immutability

가능한 한 불변 자료구조를 사용해 의도치 않은 mutation과 side effect를 막는다. "이 값이 함수에 넘긴 사이 어디선가 바뀌었나?"라는 추적 비용 자체가 사라지므로 디버깅과 동시성 안전성이 좋아진다.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class User:
    name: str
    email: str

from typing import NamedTuple

class Point(NamedTuple):
    x: float
    y: float
```

- 도메인 값 객체(Value Object)는 기본적으로 `@dataclass(frozen=True)` 또는 `NamedTuple`.
- 변경이 필요한 모델만 일반 dataclass / 일반 클래스. "왜 mutable인가"를 설명할 수 있어야 한다.
- 컬렉션 반환은 가능하면 `tuple` / `frozenset` — 호출자가 mutate 못하도록.

## 임포트

- 한 줄에 하나씩, alias 없이 직접 import 선호. (Google pyguide 권장)
- 그룹 순서:
  1. 표준 라이브러리
  2. 서드파티
  3. 로컬 (애플리케이션)
- 각 그룹 사이 빈 줄 1개. Ruff `I` (isort) 룰이 자동 정렬.

```python
import os
import sys

import requests
from pydantic import BaseModel

from myapp.services import user_service
```

- **`from X import *` 금지** — 이름 충돌과 IDE 추적성 저하.

## 함수

- 함수 인자가 5개 이상이면 dataclass/`TypedDict`/`pydantic.BaseModel`로 분해.
- **타입 힌트 필수** (`def foo(x: int) -> str:`). 내부 헬퍼라도 권장.
- 키워드 전용 인자(`*` 뒤)를 적극 활용해 호출 시 의도를 명확히:

```python
def send_email(*, to: str, subject: str, body: str) -> None: ...

# ✗ 모호함
send_email("a@b.com", "hi", "...")

# ○ 명확
send_email(to="a@b.com", subject="hi", body="...")
```

## Docstring (Google convention)

```python
def fetch_user(user_id: int) -> User:
    """사용자 ID로 User 객체를 조회한다.

    Args:
        user_id: 조회할 사용자의 정수 ID.

    Returns:
        조회된 User 객체. 캐시 hit 시 stale일 수 있다.

    Raises:
        UserNotFoundError: user_id에 해당하는 사용자가 없을 때.
    """
```

- 모듈/패키지 docstring은 권장이지만 NXN 기본 config에서는 누락 허용 (`D100`, `D104` ignore).
- 테스트 파일은 docstring 면제 (`tests/**/*.py = ["D"]`).
- 한 줄 docstring은 따옴표 같은 줄에 닫는다: `"""한 줄 설명."""`

## 비교

- `==` vs `is`: 값 비교는 `==`, identity 비교(특히 `None`)는 `is`.

```python
if user is None: ...        # ○
if status == "ready": ...   # ○
```

## 예외

- 가능한 한 좁은 예외 클래스를 잡는다. `except:` 또는 `except Exception:` 남용 금지.
- 도메인 예외는 클래스로 분리 (`class ValidationError(Exception):`).
- 에러 메시지에 컨텍스트 포함 (`f"User {user_id} not found"`).

## Truthy / Boolean

- 빈 컬렉션 / `0` / `""` / `None`의 falsy 동작을 활용. 단 의도가 명확할 때만.
- "값이 없음 또는 빈 컬렉션"의 모호함이 있으면 명시적으로:

```python
# 모호: items가 None일 수도 있고 빈 list일 수도
if not items: ...

# 명확
if items is None or len(items) == 0: ...
```

## 타입 힌트

- Python 3.10+ 문법 사용: `list[int]`, `dict[str, Any]`, `X | None`.
- `Optional[X]` 대신 `X | None`. Ruff `UP` (pyupgrade) 룰이 자동 변환.
- Generic은 `from typing import TypeVar` 또는 PEP 695 (`def f[T](...)`, Python 3.12+).

## 컨텍스트 매니저 / 리소스 관리

- 파일/소켓/락은 항상 `with` 블록.
- 여러 리소스: 한 `with`에 묶거나 `contextlib.ExitStack`.

```python
with open("a.txt") as fa, open("b.txt") as fb:
    ...
```

## 함수형 vs 명령형

- 컴프리헨션은 한 줄에 한 가지 변환만. 두 단계 이상은 일반 for문이 더 읽힌다.

```python
# ✗ 두 단계 + 조건
result = [transform(x) for x in xs if predicate(x) for y in x.items]

# ○
filtered = (x for x in xs if predicate(x))
result = [transform(x) for x in filtered]
```

- `map`/`filter`보다 컴프리헨션 선호 (Google pyguide).
- `lambda`는 한 줄짜리 단순한 경우만.

## 파일 구조

- `__init__.py`의 re-export는 허용 (`F401` ignored). 패키지 공개 API 정리에 유용.
- 한 모듈에 클래스 여러 개는 응집도 높을 때만. 보통 1 모듈 = 1 책임.
- 800줄 이상이면 거의 항상 분리.

## 주석

- WHAT이 아닌 WHY. 코드가 자명하면 주석 없이.
- TODO 형식: `# TODO(name): description`.
- 한국어/영어는 프로젝트별 일관성. 도메인 용어가 한국어이면 한국어 주석이 더 정확할 수 있다.

## 참고

- Google pyguide: https://google.github.io/styleguide/pyguide.html
- PEP 8: https://peps.python.org/pep-0008/
- Ruff 자동 강제 룰: `assets/configs/pyproject.ruff.snippet` 또는 `assets/configs/ruff.toml`

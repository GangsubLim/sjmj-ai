---
paths:
  - "**/*.md"
  - "**/*.mdx"
  - "**/*.markdown"
---

# Markdown — NXN Style Guide

[Google Markdown Style Guide](https://google.github.io/styleguide/docguide/style.html)를 기반. 형식 통일은 Prettier가 처리하므로(들여쓰기, 줄바꿈, 리스트 마커 등) 이 문서는 작성 컨벤션만 다룬다.

## 파일 / 인코딩

- 확장자: `.md` (단일 표준).
- 인코딩 UTF-8, EOL `LF`, 파일 끝 newline (.editorconfig가 강제).
- 파일명: `kebab-case.md` (`user-guide.md`).

## 제목 (Heading)

- ATX 스타일 (`#`)만 사용. Setext (`====`) 금지 — Prettier가 ATX로 정규화.
- 한 문서 = `<h1>` 한 개. 그 외는 계층 유지 (`#` → `##` → `###`).
- 제목 앞뒤 빈 줄 1개씩.
- 제목에는 마침표 X. 가급적 짧고 명확하게.

```md
# 좋은 제목

## 더 작은 섹션

본문...
```

## 본문 / 줄바꿈

- 한 문장 한 줄 권장 (semantic line break) — git diff가 깨끗해진다.

```md
NXN 프로젝트는 Google Style Guide을 따른다.
도구가 자동 강제할 수 있는 영역은 Prettier가 처리한다.
사람이 따라야 할 컨벤션은 이 문서에 정리한다.
```

- 강제 줄바꿈(`<br>`)은 시 등 특수한 경우만. 보통은 빈 줄로 단락 구분.
- 한 단락이 너무 길면 (~10줄+) 분할 검토.

## 리스트

- 무순 리스트는 `-` 마커 (Prettier 기본).
- 순서 리스트는 `1.`, `2.`, `3.` (실제 숫자 또는 모두 `1.`).
- 중첩은 2 spaces 들여쓰기.
- 항목이 한 문장이면 마침표 생략, 여러 문장이면 마침표.

```md
- 짧은 항목
- 또 다른 항목

1. 첫 번째 단계입니다. 자세히 설명하면 다음과 같습니다.
2. 두 번째 단계.
```

## 코드

- 인라인 코드: 백틱 한 개 (`` `code` ``).
- 코드 블록: triple backtick + 언어 명시 (syntax highlight).

````md
```python
def foo():
    pass
```
````

- 언어 명시는 거의 모든 경우 권장. plain text는 `text` 또는 생략.
- 큰 코드 블록은 별도 파일로 빼는 게 나을 수 있다.

## 링크 / 이미지

- 인라인 형식 우선: `[text](url)`.
- 같은 링크가 반복되면 reference 형식: `[text][ref]` + `[ref]: url` (문서 끝).
- 이미지: `![alt](path)`. `alt`는 항상 의미 있게.

## 강조

- **굵게**: `**...**` (Prettier 기본; `__...__` X).
- _기울임_: `*...*`.
- `~~취소선~~`은 GitHub Flavored Markdown 한정 — 일반 문서에는 자제.
- 이모지는 의미가 명확할 때만.

## 표

```md
| 컬럼 | 설명   |
| ---- | ------ |
| a    | A 설명 |
| b    | B 설명 |
```

- 헤더는 첫 줄, 정렬은 두 번째 줄(`:---`, `:---:`, `---:`).
- 표 안에서 줄바꿈이 필요하면 `<br>` (Markdown 표는 한 셀 한 줄).

## 인용 / 콜아웃

- 인용은 `>`. 중첩은 `>>`.
- 경고/팁은 GitHub의 alert 문법 활용 (`> [!NOTE]`, `> [!WARNING]`):

```md
> [!NOTE]
> 보조 설명입니다.

> [!WARNING]
> 위험한 동작입니다.
```

## 한국어 + 영어 혼용

- 한글과 영문/숫자 사이 공백 1개 권장 (`Prettier가 자동 처리하지 않으므로 작성자 책임`).
- 코드/명령어는 인라인 코드로 감싸 한국어와 구분.
- 따옴표: 한국어 본문에는 `"..."` (이중 쌍따옴표) 권장.

## 문서 구조

- 첫 줄: `# 제목`.
- 두 번째: 1-2 문장 요약.
- 그 다음: 목차 (긴 문서 한정), 본문, 참고/링크.
- 마지막에 "참고"/"References" 섹션을 두고 외부 링크를 모은다.

## 참고

- Google Markdown Style Guide: https://google.github.io/styleguide/docguide/style.html
- CommonMark 명세: https://commonmark.org/
- GitHub Flavored Markdown: https://github.github.com/gfm/
- Prettier 자동 처리: 들여쓰기, 리스트 마커, 빈 줄

"""hermes 판독 지식(knowledge/v{N}.md) 버전 간 대조 — 순수 계층(stdlib 전용).

agent_learn.publish가 남기는 md는 ``## 절 → 그룹 라벨 → 불릿`` 3단이다. 여기서는 그
3단만 읽고 절 이름·등급 이름·형식(교정 사전 표 등)에 의미를 두지 않는다 — v1(등급 없는
품목 목록·교정 사전 표)과 v2 이후(등급 3단) 사이의 형식 전환도 같은 규칙으로 대조된다.

DB·파일시스템에 닿지 않는다 — 입력은 md 문자열, 출력은 dict/list뿐이다.
"""

import re

_PLACEHOLDER = "(없음)"


def _is_item(line: str) -> bool:
    """불릿(``- ``)·표 행(``| ``)이 항목이다. 표 구분선(``| --- |``)은 항목이 아니다."""
    if line.startswith("- "):
        return True
    return line.startswith("|") and any(ch not in "|- :" for ch in line)


def parse_knowledge(md: str) -> dict[str, list[tuple[str, str]]]:
    """md를 절별 ``(그룹 라벨, 항목 텍스트)`` 목록으로 편다.

    절 이름은 ``## `` 를 뗀 헤딩 텍스트, 그룹 라벨은 절 안에서 항목 앞에 온 마지막
    비항목 줄이다(없으면 ``""``). ``(없음)`` 자리표시자는 항목도 라벨도 아니다.

    Args:
        md: knowledge/v{N}.md 전문.

    Returns:
        {절: [(그룹, 항목), ...]} — 절·항목 모두 md 등장 순서.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    section: str | None = None
    group = ""
    for raw in md.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            section = line[3:].strip()
            out.setdefault(section, [])
            group = ""
            continue
        if section is None or not line or line == _PLACEHOLDER:
            continue
        if _is_item(line):
            out[section].append((group, line[2:].strip() if line.startswith("- ") else line))
        else:
            group = line
    return out


_REFS_RE = re.compile(r"\s*\((#\d+(?:,\s*#\d+)*)\)$")


def _key_value(text: str) -> tuple[str, str] | None:
    """항목을 ``(키, 값)`` 으로 가른다 — 같은 키·다른 값이면 삭제+추가 대신 '변경' 1건이다.

    ``라벨: 값`` 꼴은 라벨이 키(누적 교정: 44건), 서술문은 끝의 근거 ``(#…)`` 목록을 뗀
    본문이 키이고 근거가 값이다(규칙 문장에 사례 id만 늘어난 경우). 둘 다 아니면 None.
    """
    if ": " in text:
        key, value = text.split(": ", 1)
        return key.strip(), value.strip()
    m = _REFS_RE.search(text)
    if m:
        return text[: m.start()].strip(), f"({m.group(1)})"
    return None


def _diff_section(
    prev: list[tuple[str, str]], cur: list[tuple[str, str]]
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """절 1개의 항목 목록을 대조해 (added, removed, moved, changed)를 낸다."""
    prev_set, cur_set = set(prev), set(cur)
    removed = [gt for gt in prev if gt not in cur_set]
    added = [gt for gt in cur if gt not in prev_set]

    # 이동: 같은 텍스트가 같은 절의 다른 그룹으로. 등급 이동(가끔→보통)이 전형이다.
    moved = []
    removed_texts = {t: g for g, t in removed}
    for g, t in list(added):
        if t in removed_texts and removed_texts[t] != g:
            moved.append({"text": t, "from": removed_texts[t], "to": g})
            added.remove((g, t))
            removed.remove((removed_texts[t], t))

    # 변경: 같은 그룹·같은 키. 그룹이 다르면 문맥이 다르므로 접지 않는다.
    changed = []
    removed_keys = {}
    for g, t in removed:
        kv = _key_value(t)
        if kv:
            removed_keys.setdefault((g, kv[0]), (t, kv[1]))
    for g, t in list(added):
        kv = _key_value(t)
        if kv and (g, kv[0]) in removed_keys:
            old_text, old_value = removed_keys.pop((g, kv[0]))
            changed.append({"group": g, "key": kv[0], "before": old_value, "after": kv[1]})
            added.remove((g, t))
            removed.remove((g, old_text))

    return _to_dicts(added), _to_dicts(removed), moved, changed


def _to_dicts(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"group": g, "text": t} for g, t in pairs]


def diff_knowledge(prev_md: str, cur_md: str) -> list[dict]:
    """두 버전의 md를 절 단위로 대조한다. 변경이 있는 절만 낸다.

    절 순서는 현재 버전의 등장 순서 뒤에 이전 버전에만 있던 절이다.

    Args:
        prev_md: 직전 발행 버전 전문.
        cur_md: 이 버전 전문.

    Returns:
        [{section, added[], removed[], moved[], changed[]}] — 전 항목 비어 있으면 절 자체 생략.
    """
    prev, cur = parse_knowledge(prev_md), parse_knowledge(cur_md)
    sections = list(cur) + [s for s in prev if s not in cur]
    out = []
    for s in sections:
        added, removed, moved, changed = _diff_section(prev.get(s, []), cur.get(s, []))
        if added or removed or moved or changed:
            out.append(
                {
                    "section": s,
                    "added": added,
                    "removed": removed,
                    "moved": moved,
                    "changed": changed,
                }
            )
    return out

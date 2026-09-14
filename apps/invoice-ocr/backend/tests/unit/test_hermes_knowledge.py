"""hermes 판독 지식 버전 대조 — 순수 계층 단위테스트 (DB·파일시스템 비의존).

agent_learn이 발행하는 knowledge/v{N}.md는 ``## 절 → 그룹 라벨 → 불릿`` 3단이다. 파서는
특정 형식(등급 유무·교정 사전 표)에 묶이지 않고 이 3단만 읽는다.
"""

from app.services.hermes_knowledge import diff_knowledge, parse_knowledge

MD = """# sjmj 판독 지식

## 확정 어휘

품목 — 최근 12개월 등장 등급
자주(10회 이상)
- 경유1차 (EA)
- 공임 (EA)
보통(3~9회)
- 가스 (EA)

거래처
- 성우항공

## 거래처 프로필

(없음)

## 데이터 현황

- 누적 교정: 44건
"""


def test_parse_splits_sections_by_heading():
    parsed = parse_knowledge(MD)
    assert list(parsed) == ["확정 어휘", "거래처 프로필", "데이터 현황"]


def test_parse_assigns_bullets_to_preceding_group_label():
    parsed = parse_knowledge(MD)
    assert parsed["확정 어휘"] == [
        ("자주(10회 이상)", "경유1차 (EA)"),
        ("자주(10회 이상)", "공임 (EA)"),
        ("보통(3~9회)", "가스 (EA)"),
        ("거래처", "성우항공"),
    ]


def test_parse_bullet_without_group_has_empty_group():
    assert parse_knowledge(MD)["데이터 현황"] == [("", "누적 교정: 44건")]


def test_parse_ignores_none_placeholder():
    assert parse_knowledge(MD)["거래처 프로필"] == []


# --- 버전 간 대조 ---


def _md(*sections: tuple[str, str]) -> str:
    return "# sjmj 판독 지식\n\n" + "\n".join(f"## {h}\n\n{body}\n" for h, body in sections)


def test_diff_reports_added_and_removed_items_per_section():
    prev = _md(("일반화 규칙", "- 규칙 A\n- 규칙 B"))
    cur = _md(("일반화 규칙", "- 규칙 B\n- 규칙 C"))
    assert diff_knowledge(prev, cur) == [
        {
            "section": "일반화 규칙",
            "added": [{"group": "", "text": "규칙 C"}],
            "removed": [{"group": "", "text": "규칙 A"}],
            "moved": [],
            "changed": [],
        }
    ]


def test_diff_omits_sections_without_change():
    prev = _md(("확정 어휘", "- 공임 (EA)"), ("일반화 규칙", "- 규칙 A"))
    cur = _md(("확정 어휘", "- 공임 (EA)"), ("일반화 규칙", "- 규칙 B"))
    assert [d["section"] for d in diff_knowledge(prev, cur)] == ["일반화 규칙"]


def test_diff_identical_versions_is_empty():
    md = _md(("확정 어휘", "- 공임 (EA)"))
    assert diff_knowledge(md, md) == []


def test_diff_folds_group_change_into_moved():
    prev = _md(("확정 어휘", "보통(3~9회)\n- 공임 (EA)\n가끔(2회)\n- 챔바 (EA)"))
    cur = _md(("확정 어휘", "보통(3~9회)\n- 공임 (EA)\n- 챔바 (EA)\n가끔(2회)"))
    (d,) = diff_knowledge(prev, cur)
    assert d["added"] == [] and d["removed"] == []
    assert d["moved"] == [{"text": "챔바 (EA)", "from": "가끔(2회)", "to": "보통(3~9회)"}]


def test_diff_folds_label_value_change_into_changed():
    prev = _md(("데이터 현황", "- 누적 교정: 44건\n- 초안(원장): 13건"))
    cur = _md(("데이터 현황", "- 누적 교정: 50건\n- 초안(원장): 13건"))
    (d,) = diff_knowledge(prev, cur)
    assert d["added"] == [] and d["removed"] == []
    assert d["changed"] == [{"group": "", "key": "누적 교정", "before": "44건", "after": "50건"}]


def test_diff_folds_reference_only_change_into_changed():
    prev = _md(("일반화 규칙", "- 합계를 다시 계산한다. (#580, #582)"))
    cur = _md(("일반화 규칙", "- 합계를 다시 계산한다. (#580, #582, #584)"))
    (d,) = diff_knowledge(prev, cur)
    assert d["added"] == [] and d["removed"] == []
    assert d["changed"] == [
        {
            "group": "",
            "key": "합계를 다시 계산한다.",
            "before": "(#580, #582)",
            "after": "(#580, #582, #584)",
        }
    ]


def test_diff_keeps_same_key_in_different_groups_as_add_remove():
    """같은 라벨이라도 그룹이 다르면 changed로 접지 않는다(그룹이 곧 문맥이다)."""
    prev = _md(("금액 오류 통계", "- 기타(other): 4\n최근 예시\n- #1 x: 1 → 2 (other)"))
    cur = _md(("금액 오류 통계", "- 기타(other): 5\n최근 예시\n- #2 x: 3 → 4 (other)"))
    (d,) = diff_knowledge(prev, cur)
    assert d["changed"] == [{"group": "", "key": "기타(other)", "before": "4", "after": "5"}]
    assert d["added"] == [{"group": "최근 예시", "text": "#2 x: 3 → 4 (other)"}]
    assert d["removed"] == [{"group": "최근 예시", "text": "#1 x: 1 → 2 (other)"}]


def test_diff_section_dropped_entirely_lists_all_items_removed():
    prev = _md(
        ("교정 사전", "| 오독 | 정답 |\n| --- | --- |\n| 킹핀교환 | 히타 |"),
        ("확정 어휘", "- 공임 (EA)"),
    )
    cur = _md(("확정 어휘", "- 공임 (EA)"))
    (d,) = diff_knowledge(prev, cur)
    assert d["section"] == "교정 사전"
    assert [r["text"] for r in d["removed"]] == ["| 오독 | 정답 |", "| 킹핀교환 | 히타 |"]


def test_diff_section_order_follows_current_then_previous_only():
    prev = _md(("교정 사전", "- x"), ("확정 어휘", "- a"))
    cur = _md(("확정 어휘", "- b"), ("관례 약칭", "- y"))
    assert [d["section"] for d in diff_knowledge(prev, cur)] == [
        "확정 어휘",
        "관례 약칭",
        "교정 사전",
    ]

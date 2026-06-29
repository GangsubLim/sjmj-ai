"""초안(result_json) vs 확정 payload diff → correction_json. 순수함수."""


def build_correction(result_json: dict, final_items: list[dict]) -> dict:
    """crop_ref로 초안 행과 최종 item을 매칭해 라벨·공급가 변경을 기록한다.

    - crop_ref 없는 최종 item = 사람이 추가한 행(rows_added)
    - 최종 payload에서 매칭 안 된 초안 crop = 사람이 버린 행(rows_dropped)
    """
    draft_by_ref = {r["crop_ref"]: r for r in result_json.get("rows", []) if r.get("crop_ref")}
    lines: list[dict] = []
    matched: set[str] = set()
    rows_added = 0

    for item in final_items:
        ref = item.get("crop_ref")
        if ref and ref in draft_by_ref:
            matched.add(ref)
            row = draft_by_ref[ref]
            top5 = row.get("item_top5") or []
            draft_label = top5[0]["label"] if top5 else None
            final_label = item.get("name")
            draft_supply = row.get("supply")
            final_supply = item.get("supply")
            lines.append(
                {
                    "crop_ref": ref,
                    "draft_label": draft_label,
                    "final_label": final_label,
                    "label_changed": draft_label != final_label,
                    "draft_supply": draft_supply,
                    "final_supply": final_supply,
                    "supply_changed": draft_supply != final_supply,
                }
            )
        else:
            rows_added += 1

    rows_dropped = sum(1 for ref in draft_by_ref if ref not in matched)
    return {"lines": lines, "rows_added": rows_added, "rows_dropped": rows_dropped}

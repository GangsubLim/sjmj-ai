"""초안(result_json) vs 확정 payload diff → correction_json. 순수함수."""


def build_correction(result_json: dict, final_items: list[dict]) -> dict:
    """crop_ref로 초안 행과 최종 item을 매칭해 라벨·공급가 변경을 기록한다.

    - crop_ref 없는 최종 item = 사람이 추가한 행(rows_added)
    - 최종 payload에서 매칭 안 된 초안 crop = 사람이 버린 행(rows_dropped)
    - label_source: 클라이언트가 보낸 UI 조작 출처(없으면 None). 검증은 라우터(Pydantic)가 한다.
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
                    # UI 조작 출처(어떤 경로로 이 라벨을 확정했는가). 품목 DB 존재 여부가 아니다 —
                    # 그건 training_pairs.canonical_label ⨝ item_suggestions.item_name으로 사후
                    # 관측 가능하므로 저장하지 않는다. 값 목록은 app/schemas/ocr.py가 소유한다.
                    # 초안(draft_label·top5)과의 정합성은 의도적으로 확인하지 않는다 —
                    # "클라이언트가 주장한 조작 출처"를 그대로 보존하는 것이 이 필드의 정의이고,
                    # 서버가 추론으로 덮으면 재학습 분석에서 관측값과 추정값을 구분할 수 없게 된다.
                    # 모순(top1_kept인데 label_changed 등)은 correction_json에 draft/final이 함께
                    # 남으므로 사후 쿼리로 감사 가능하다.
                    "label_source": item.get("label_source"),
                }
            )
        else:
            rows_added += 1

    rows_dropped = sum(1 for ref in draft_by_ref if ref not in matched)
    return {"lines": lines, "rows_added": rows_added, "rows_dropped": rows_dropped}


def build_training_pairs(job_id: int, invoice_id: int, correction: dict) -> list[dict]:
    """Correction lines[]를 training_pairs insert dict 리스트로 변환한다.

    crop_ref 있는 행(매칭된 초안 행)만 학습 후보다. canonical_label 초기값은
    final_label, status는 included.

    Args:
        job_id: 확정된 OCR 잡 id.
        invoice_id: confirm으로 생성된 invoice id.
        correction: build_correction 반환 dict (lines[] 보유).

    Returns:
        training_pairs insert용 dict 리스트(crop_ref 없는 line 제외).
    """
    pairs: list[dict] = []
    for line in correction.get("lines", []):
        ref = line.get("crop_ref")
        if not ref:
            continue
        final_label = line.get("final_label")
        pairs.append(
            {
                "crop_ref": ref,
                "job_id": job_id,
                "invoice_id": invoice_id,
                "row_index": int(ref.rsplit("/row-", 1)[-1]),
                "draft_label": line.get("draft_label"),
                "final_label": final_label,
                "canonical_label": final_label,
                "supply": line.get("final_supply"),
                "status": "included",
            }
        )
    return pairs

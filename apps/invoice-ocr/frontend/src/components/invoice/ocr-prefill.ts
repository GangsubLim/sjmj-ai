import type { InvoiceItem } from "@/types/invoice";
import type { OcrItemPred, OcrResult } from "@/types/ocr";
import { TOP_K } from "@/utils/label-source";

export function rowsToItems(result: OcrResult): Partial<InvoiceItem>[] {
  return result.rows.map((row) => ({
    name: row.item_top5[0]?.label ?? "",
    unit_price: row.supply ?? 0,
    quantity: 1,
    crop_ref: row.crop_ref,
    deduction: false,
  }));
}

// OCR 메타는 InvoiceItem(저장 대상)에 섞지 않는다 — 저장 payload를 오염시키지 않기 위해
// crop_ref를 키로 한 별도 맵으로 들고 다닌다(spec §2-1, docs/work/2026-07/2026-07-28-ocr-candidate-selection/spec.md
// — git 비추적, 없으면 Issue #22를 본다).
// candidates/반환 Map은 useOcrInfer의 React state와 참조를 공유한다 — 필드 전체를 readonly로
// 선언해 소비자의 in-place mutate(candidates.sort(), meta.uncertain = true)로 state가 몰래
// 바뀌는 것을 컴파일 타임에 막는다(ReadonlyMap.get()이 돌려주는 객체까지 닫아야 의미가 있다).
export interface OcrItemMeta {
  readonly candidates: readonly OcrItemPred[];
  readonly uncertain: boolean;
  readonly jobId: number;
  readonly rowIndex: number;
}

export function rowsToOcrMeta(
  result: OcrResult,
  jobId: number,
): ReadonlyMap<string, OcrItemMeta> {
  return new Map(
    result.rows.map((row) => [
      row.crop_ref,
      {
        // 외부 데이터 경계 — 서버가 TOP_K를 넘는 후보를 주더라도 여기서 잘라내
        // 화이트리스트 밖 rank(candidate_picked:TOP_K 이상)가 만들어지지 않게 한다.
        candidates: row.item_top5.slice(0, TOP_K),
        // 레거시 잡(#22 이전 result_json)에는 item_uncertain이 없다. 후보가 0개인 행은
        // 생산자(ml infer_job._is_item_uncertain)가 항상 미확신으로 판정하는 구간이라
        // 플래그 없이도 확정할 수 있다 — 가장 손봐야 할 행이 조용히 넘어가지 않도록
        // 그 구간만 fail-closed로 폴백한다(나머지는 기존 계약대로 확신).
        uncertain: row.item_uncertain ?? row.item_top5.length === 0,
        jobId,
        rowIndex: row.row_index,
      },
    ]),
  );
}

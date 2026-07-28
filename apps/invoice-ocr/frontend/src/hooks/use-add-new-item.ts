import { useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";

import type { Item } from "@/types/item";
import { itemSuggestionsAPI } from "@/services/api";

interface ErrorEnvelope {
  error?: { message?: string };
}

function getErrorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const data = e.response?.data as ErrorEnvelope | undefined;
    if (data?.error?.message) return data.error.message;
  }
  if (e instanceof Error) return e.message;
  return "품목 등록에 실패했습니다";
}

/**
 * 품목 DB에 새 이름을 등록한다. 성공 시 생성된 Item, 실패 시 null.
 *
 * 상태 갱신은 호출자가 소유한다 — 실패 시 호출자가 아무것도 하지 않으므로
 * 사용자가 타이핑한 이름이 화면에 그대로 남는다(spec §2-3).
 */
export function useAddNewItem(): (name: string) => Promise<Item | null> {
  return useCallback(async (name: string) => {
    try {
      const res = await itemSuggestionsAPI.add({ item_name: name.trim() });
      toast.success(
        "품목이 등록되었습니다 (자동완성 즉시 반영 · OCR 추천은 다음 뱅크 갱신 후)",
      );
      return res.data;
    } catch (e) {
      toast.error(getErrorMessage(e));
      return null;
    }
  }, []);
}

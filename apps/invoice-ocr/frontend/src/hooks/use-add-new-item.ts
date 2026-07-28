import { useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";

import type { Item } from "@/types/item";
import { itemSuggestionsAPI } from "@/services/api";

interface ErrorEnvelope {
  error?: { message?: string };
}

const FALLBACK_MESSAGE = "품목 등록에 실패했습니다";

function getErrorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const status = e.response?.status;
    const data = e.response?.data as ErrorEnvelope | undefined;
    // 서버 메시지는 4xx에서만 신뢰한다 — 5xx의 error.message는 str(exc)(파이썬 예외
    // 문자열)이고, 응답 자체가 없는 축(네트워크 단절·프록시 오류)은 'Network Error'
    // 같은 영문이 그대로 한국어 UI에 뜬다.
    if (status != null && status < 500 && data?.error?.message) {
      return data.error.message;
    }
    return FALLBACK_MESSAGE;
  }
  if (e instanceof Error) return e.message;
  return FALLBACK_MESSAGE;
}

/**
 * 품목 DB에 새 이름을 등록한다. 성공 시 생성된 Item, 실패 시 null.
 *
 * 상태 갱신은 호출자가 소유한다 — 실패 시 호출자가 아무것도 하지 않으므로
 * 사용자가 타이핑한 이름이 화면에 그대로 남는다(spec §2-3).
 */
export function useAddNewItem(): (name: string) => Promise<Item | null> {
  return useCallback(async (name: string) => {
    const trimmed = name.trim();
    // 호출 지점(Autocomplete)의 게이트는 inputValue truthy뿐이라 '   '도 통과한다.
    // 백엔드 Validator가 거부하긴 하지만, 왕복 한 번을 낭비하고 운영자에게는 필드명이
    // 박힌 개발자용 메시지가 뜬다 — 경계에서 먼저 막는다.
    if (!trimmed) {
      toast.error("품목명을 입력해주세요");
      return null;
    }
    try {
      const res = await itemSuggestionsAPI.add({ item_name: trimmed });
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

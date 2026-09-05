// 큐레이션 목록/상세 URL 조립. page=1·필터 off면 쿼리를 붙이지 않는다 — 현재 URL과 같은
// 모양을 유지해 "1페이지에서 진입"과 "쿼리 없이 북마크"가 같은 주소가 된다(spec §3 URL 계약).
// 행 증감 필터도 여기서 함께 실린다 — 상세 진입·이전/다음 이동에서 필터가 풀리면
// S1이 만들려는 작업 큐가 상세에서 끊긴다(spec §4-2).

/** 행 증감 필터의 URL 파라미터 이름·켜짐 값. 목록·상세·이웃 이동·API 요청이 같은 표기를 쓴다. */
export const ROW_DELTA_PARAM = "row_delta";
export const ROW_DELTA_ON = "true";

/** 정확히 "true"만 켜짐으로 본다 — 표기를 하나로 고정해 다른 철자가 조용히 무시되는 자리를 없앤다. */
export function parseRowDelta(raw: string | null): boolean {
  return raw === ROW_DELTA_ON;
}

export interface JobUrlOptions {
  rowDelta?: boolean;
}

function query(page: number, options: JobUrlOptions): string {
  const params = new URLSearchParams();
  if (page > 1) params.set("page", String(page));
  if (options.rowDelta) params.set(ROW_DELTA_PARAM, ROW_DELTA_ON);
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** 목록 URL. basePath는 "/curation" 또는 "/curation/pending"(후행 슬래시 없음). */
export function jobListUrl(
  basePath: string,
  page: number,
  options: JobUrlOptions = {},
): string {
  return `${basePath}${query(page, options)}`;
}

/** 상세 URL. page는 "이 잡이 속한 목록 페이지"이며 목록 복귀에 쓰인다. */
export function jobDetailUrl(
  basePath: string,
  jobId: number,
  page: number,
  options: JobUrlOptions = {},
): string {
  return `${basePath}/${jobId}${query(page, options)}`;
}

// 큐레이션 목록/상세 URL 조립. page=1이면 쿼리를 붙이지 않는다 — 현재 URL과 같은 모양을
// 유지해 "1페이지에서 진입"과 "쿼리 없이 북마크"가 같은 주소가 된다(spec §3 URL 계약).

/** 목록 URL. basePath는 "/curation" 또는 "/curation/pending"(후행 슬래시 없음). */
export function jobListUrl(basePath: string, page: number): string {
  return page > 1 ? `${basePath}?page=${page}` : basePath;
}

/** 상세 URL. page는 "이 잡이 속한 목록 페이지"이며 목록 복귀에 쓰인다. */
export function jobDetailUrl(
  basePath: string,
  jobId: number,
  page: number,
): string {
  return page > 1
    ? `${basePath}/${jobId}?page=${page}`
    : `${basePath}/${jobId}`;
}

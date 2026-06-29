export interface Pagination {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
}

export interface ListResponse<T> {
  success?: boolean;
  data: T[];
  /** modern 구조화 envelope(FastAPI, 1B B안 확정) — total은 여기 중첩된다. */
  pagination?: Pagination;
  /** legacy 평평 형태(mock 픽스처) 호환 필드 — 런타임 mock 비활성. */
  total?: number;
  page?: number;
  limit?: number;
}

export interface SingleResponse<T> {
  success?: boolean;
  data: T;
  message?: string;
}

export interface ErrorResponse {
  error: string;
  message: string;
  status: number;
}

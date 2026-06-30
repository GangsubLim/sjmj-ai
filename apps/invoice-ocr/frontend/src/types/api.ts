export interface Pagination {
  page: number;
  limit: number;
  total: number;
  totalPages: number;
}

export interface ListResponse<T> {
  success?: boolean;
  data: T[];
  /** 구조화 envelope — total은 pagination에 중첩된다. */
  pagination?: Pagination;
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

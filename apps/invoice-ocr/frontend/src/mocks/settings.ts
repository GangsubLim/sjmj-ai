import type { Issuer, AppSettings } from "@/types/settings";

export const mockIssuer: Issuer = {
  id: 1,
  company_name: "SJMJ 자동차정비",
  representative: "김성준",
  business_number: "128-34-56789",
  address: "서울시 강서구 화곡로 264 1층",
  business_type: "서비스업",
  business_item: "자동차 정비, 부품 판매",
  phone: "02-2345-6789",
  fax: "02-2345-6780",
  bank_account: "국민은행 123-456-789012 (주)SJMJ",
  tel_fax: "02-2345-6789 / 02-2345-6780",
  show_sjdojang: true,
  stamp_image_url: undefined,
};

export const mockAppSettings: AppSettings = {
  default_vat_rate: 10,
  default_document_title: "거래명세서",
  pdf_filename_pattern: "{recipient}_{date}",
};

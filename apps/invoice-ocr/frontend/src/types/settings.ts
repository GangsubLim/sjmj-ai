export interface Issuer {
  id?: number;
  company_name: string;
  representative: string;
  business_number: string;
  address: string;
  business_type: string;
  business_item: string;
  phone: string;
  fax: string;
  bank_account: string;
  /** @deprecated Use phone/fax combination instead */
  tel_fax: string;
  show_sjdojang: boolean;
  stamp_image_url?: string;
}

export interface AppSettings {
  default_vat_rate: number;
  default_document_title: string;
  pdf_filename_pattern: string;
}

export const DEFAULT_APP_SETTINGS: AppSettings = {
  default_vat_rate: 10,
  default_document_title: "거래명세서",
  pdf_filename_pattern: "{recipient}_{date}",
};

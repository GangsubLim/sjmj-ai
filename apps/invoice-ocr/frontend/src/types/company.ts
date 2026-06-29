export type SmsNumberType = "phone" | "fax";

export interface Company {
  id?: number;
  company_name: string;
  recipient2?: string;
  phone?: string;
  fax?: string;
  sms_number_type?: SmsNumberType;
  address?: string;
  business_number?: string;
  notes?: string;
  usage_count?: number;
  last_used?: string;
  created_at?: string;
  updated_at?: string;
}

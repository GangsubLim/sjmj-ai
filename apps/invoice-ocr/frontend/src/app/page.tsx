import { useLocation } from "react-router-dom";
import { InvoiceForm } from "@/components/invoice";
import type { Invoice } from "@/types/invoice";

type CreatePageLocationState = {
  cloneData?: Invoice;
};

export default function InvoiceCreatePage() {
  const location = useLocation();
  const state = location.state as CreatePageLocationState | null;

  return <InvoiceForm mode="create" initialData={state?.cloneData} />;
}

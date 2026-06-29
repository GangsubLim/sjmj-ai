import { useParams } from "react-router-dom";
import { PageContainer, PageHeader } from "@/components/layout";
import { InvoiceForm } from "@/components/invoice";
import { useInvoice } from "@/hooks/use-invoices";
import { Skeleton } from "@/components/ui/skeleton";

export default function InvoiceEditPage() {
  const { id } = useParams();
  const {
    data: invoice,
    loading,
    error,
  } = useInvoice(id ? Number(id) : undefined);

  if (loading) {
    return (
      <>
        <PageHeader title="거래명세서 수정" />
        <PageContainer className="space-y-4 py-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-32 w-full" />
        </PageContainer>
      </>
    );
  }

  if (error || !invoice) {
    return (
      <>
        <PageHeader title="거래명세서 수정" />
        <PageContainer className="py-4">
          <p className="text-destructive">
            {error ?? "거래명세서를 찾을 수 없습니다"}
          </p>
        </PageContainer>
      </>
    );
  }

  return <InvoiceForm mode="edit" initialData={invoice} />;
}

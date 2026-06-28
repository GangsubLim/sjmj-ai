import * as React from "react";
import { PlusIcon, UsersIcon } from "lucide-react";

import type { Company } from "@/types/company";
import { useCompanies } from "@/hooks/use-companies";
import { useDebounce } from "@/hooks/use-debounce";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useMasterDetail } from "@/hooks/use-master-detail";
import { companySuggestionsAPI } from "@/services/api";
import { getCompanySmsTargetLabel } from "@/utils/formatters";

import { PageContainer, PageHeader } from "@/components/layout";
import { cn } from "@/lib/utils";
import { SearchInput } from "@/components/ui/search-input";
import { FilterChips } from "@/components/ui/filter-chips";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { CustomerCard } from "@/components/company";
import { CustomerForm } from "@/components/company";

const FILTER_OPTIONS = [
  { label: "전체", value: "all" },
  { label: "자주 사용", value: "high_freq" },
  { label: "최근", value: "recent" },
];

export default function CompanyManagePage() {
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [filter, setFilter] = React.useState("all");
  const { data: companies, loading, refetch } = useCompanies();

  const {
    editTarget,
    setEditTarget,
    showForm,
    setShowForm,
    deleteTarget,
    setDeleteTarget,
    selected: selectedCompany,
    setSelected: setSelectedCompany,
    handleDelete,
    handleFormSaved,
  } = useMasterDetail<Company>({
    deleteFn: companySuggestionsAPI.delete,
    refetch,
    deleteSuccessMessage: "거래처가 삭제되었습니다",
  });

  const filteredCompanies = React.useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();
    let list = [...companies];

    if (q) {
      list = list.filter((c) =>
        [c.company_name, c.recipient2, c.phone, c.business_number, c.address]
          .filter(Boolean)
          .some((value) => value!.toLowerCase().includes(q)),
      );
    }

    if (filter === "high_freq") {
      list = list.filter((c) => (c.usage_count ?? 0) >= 5);
    } else if (filter === "recent") {
      list.sort((a, b) => (b.last_used ?? "").localeCompare(a.last_used ?? ""));
    }
    return list;
  }, [companies, filter, debouncedSearch]);

  if (!isDesktop && (showForm || editTarget)) {
    return (
      <CustomerForm
        initial={editTarget ?? undefined}
        onSaved={handleFormSaved}
        onCancel={() => {
          setShowForm(false);
          setEditTarget(null);
        }}
      />
    );
  }

  return (
    <>
      <PageHeader title="거래처 관리" showBack={false} />
      <PageContainer className="py-4">
        <div className="hidden lg:flex lg:items-center lg:justify-between lg:pb-4">
          <h1 className="text-xl font-semibold">거래처 관리</h1>
        </div>
        <div className="lg:grid lg:h-[calc(100dvh-var(--page-shell-offset))] lg:grid-cols-[2fr_3fr] lg:gap-6">
          {/* 좌측: 리스트 (항상 표시) */}
          <div className="flex flex-col space-y-4 lg:min-h-0">
            <div className="shrink-0 space-y-4">
              <SearchInput
                value={search}
                onChange={setSearch}
                placeholder="거래처명 검색"
              />
              <Button
                className="w-full"
                onClick={() => {
                  setShowForm(true);
                  setEditTarget(null);
                  setSelectedCompany(null);
                }}
              >
                <PlusIcon className="mr-1 size-4" aria-hidden="true" />새 거래처
                추가
              </Button>
              <FilterChips
                options={FILTER_OPTIONS}
                value={filter}
                onChange={setFilter}
              />
            </div>

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full rounded-xl" />
                ))}
              </div>
            ) : filteredCompanies.length === 0 ? (
              <EmptyState
                icon={UsersIcon}
                title="거래처가 없습니다"
                description="새 거래처를 추가해보세요"
                action={{
                  label: "거래처 추가",
                  onClick: () => setShowForm(true),
                }}
              />
            ) : (
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
                {filteredCompanies.map((company) => (
                  <CustomerCard
                    key={company.id}
                    company={company}
                    onClick={() => {
                      if (isDesktop) {
                        setSelectedCompany(company);
                        setEditTarget(null);
                        setShowForm(false);
                      } else {
                        setEditTarget(company);
                      }
                    }}
                    className={cn(
                      isDesktop &&
                        selectedCompany?.id === company.id &&
                        "ring-primary ring-2 ring-offset-2",
                    )}
                  />
                ))}
              </div>
            )}
          </div>

          {/* 우측: PC 전용 상세/편집 패널 */}
          <div className="hidden lg:block lg:max-h-full lg:self-start lg:overflow-y-auto">
            {showForm || editTarget ? (
              <CustomerForm
                variant="panel"
                initial={editTarget ?? undefined}
                onSaved={() => {
                  handleFormSaved();
                  setSelectedCompany(null);
                }}
                onCancel={() => {
                  setShowForm(false);
                  setEditTarget(null);
                }}
              />
            ) : selectedCompany ? (
              <div className="rounded-xl border p-4">
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="text-lg font-semibold">
                    {selectedCompany.company_name}
                  </h2>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setEditTarget(selectedCompany)}
                    >
                      편집
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10"
                      onClick={() =>
                        selectedCompany.id &&
                        setDeleteTarget(selectedCompany.id)
                      }
                    >
                      삭제
                    </Button>
                  </div>
                </div>
                <dl className="space-y-2 text-sm">
                  {selectedCompany.recipient2 && (
                    <div>
                      <dt className="text-muted-foreground text-xs">
                        수신참조
                      </dt>
                      <dd>{selectedCompany.recipient2}</dd>
                    </div>
                  )}
                  {selectedCompany.phone && (
                    <div>
                      <dt className="text-muted-foreground text-xs">연락처</dt>
                      <dd>{selectedCompany.phone}</dd>
                    </div>
                  )}
                  {selectedCompany.fax && (
                    <div>
                      <dt className="text-muted-foreground text-xs">FAX</dt>
                      <dd>{selectedCompany.fax}</dd>
                    </div>
                  )}
                  <div>
                    <dt className="text-muted-foreground text-xs">문자 선택</dt>
                    <dd>
                      {getCompanySmsTargetLabel(
                        selectedCompany,
                        selectedCompany.sms_number_type,
                      )}
                    </dd>
                  </div>
                  {selectedCompany.business_number && (
                    <div>
                      <dt className="text-muted-foreground text-xs">
                        사업자번호
                      </dt>
                      <dd>{selectedCompany.business_number}</dd>
                    </div>
                  )}
                  {selectedCompany.address && (
                    <div>
                      <dt className="text-muted-foreground text-xs">주소</dt>
                      <dd>{selectedCompany.address}</dd>
                    </div>
                  )}
                </dl>
              </div>
            ) : (
              <EmptyState
                icon={UsersIcon}
                title="거래처를 선택하세요"
                description="좌측 목록에서 거래처를 클릭하세요"
              />
            )}
          </div>
        </div>
      </PageContainer>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={() => setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>거래처 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              이 거래처를 삭제하시겠습니까?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>취소</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteTarget) handleDelete(deleteTarget);
                setDeleteTarget(null);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              삭제
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

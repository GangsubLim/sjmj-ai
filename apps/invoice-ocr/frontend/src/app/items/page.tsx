import * as React from "react";
import { PlusIcon, PackageIcon } from "lucide-react";

import type { Item } from "@/types/item";
import { useItems } from "@/hooks/use-items";
import { useDebounce } from "@/hooks/use-debounce";
import { useMediaQuery } from "@/hooks/use-media-query";
import { useMasterDetail } from "@/hooks/use-master-detail";
import { itemSuggestionsAPI } from "@/services/api";

import { cn } from "@/lib/utils";
import { PageContainer, PageHeader } from "@/components/layout";
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
import { ItemTemplateCard } from "@/components/item";
import { ItemTemplateForm } from "@/components/item";

const CATEGORY_FILTERS = [
  { label: "전체", value: "all" },
  { label: "오일", value: "oil" },
  { label: "타이어", value: "tires" },
  { label: "부품", value: "parts" },
  { label: "공임", value: "labor" },
];

export default function ItemManagePage() {
  const isDesktop = useMediaQuery("(min-width: 1024px)");
  const [search, setSearch] = React.useState("");
  const debouncedSearch = useDebounce(search, 300);
  const [category, setCategory] = React.useState("all");
  const { data: items, loading, refetch } = useItems();

  const {
    editTarget,
    setEditTarget,
    showForm,
    setShowForm,
    deleteTarget,
    setDeleteTarget,
    selected: selectedItem,
    setSelected: setSelectedItem,
    handleDelete,
    handleFormSaved,
  } = useMasterDetail<Item>({
    deleteFn: itemSuggestionsAPI.delete,
    refetch,
    deleteSuccessMessage: "품목이 삭제되었습니다",
  });

  const filteredItems = React.useMemo(() => {
    const q = debouncedSearch.trim().toLowerCase();

    return items.filter((item) => {
      const matchesCategory =
        category === "all" ? true : item.category === category;

      if (!matchesCategory) return false;
      if (!q) return true;

      return [item.item_name, item.category, item.notes]
        .filter(Boolean)
        .some((value) => value!.toLowerCase().includes(q));
    });
  }, [items, category, debouncedSearch]);

  if (!isDesktop && (showForm || editTarget)) {
    return (
      <ItemTemplateForm
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
      <PageHeader title="품목 관리" showBack={false} />
      <PageContainer className="py-4">
        <div className="hidden lg:flex lg:items-center lg:justify-between lg:pb-4">
          <h1 className="text-xl font-semibold">품목 관리</h1>
        </div>
        <div className="lg:grid lg:h-[calc(100dvh-var(--page-shell-offset))] lg:grid-cols-[2fr_3fr] lg:gap-6">
          {/* 좌측: 리스트 */}
          <div className="flex flex-col space-y-4 lg:min-h-0">
            <div className="shrink-0 space-y-4">
              <SearchInput
                value={search}
                onChange={setSearch}
                placeholder="품목명 검색"
              />
              <Button
                className="w-full"
                onClick={() => {
                  setShowForm(true);
                  setEditTarget(null);
                  setSelectedItem(null);
                }}
              >
                <PlusIcon className="mr-1 size-4" aria-hidden="true" />새 품목
                추가
              </Button>
              <FilterChips
                options={CATEGORY_FILTERS}
                value={category}
                onChange={setCategory}
              />
            </div>

            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 w-full rounded-xl" />
                ))}
              </div>
            ) : filteredItems.length === 0 ? (
              <EmptyState
                icon={PackageIcon}
                title="품목이 없습니다"
                description="새 품목 템플릿을 추가해보세요"
                action={{
                  label: "품목 추가",
                  onClick: () => setShowForm(true),
                }}
              />
            ) : (
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto">
                {filteredItems.map((item) => (
                  <ItemTemplateCard
                    key={item.id}
                    item={item}
                    onClick={() => {
                      if (isDesktop) {
                        setSelectedItem(item);
                        setEditTarget(null);
                        setShowForm(false);
                      } else {
                        setEditTarget(item);
                      }
                    }}
                    onDelete={() => item.id != null && setDeleteTarget(item.id)}
                    className={cn(
                      isDesktop &&
                        selectedItem?.id === item.id &&
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
              <ItemTemplateForm
                variant="panel"
                initial={editTarget ?? undefined}
                onSaved={() => {
                  handleFormSaved();
                  setSelectedItem(null);
                }}
                onCancel={() => {
                  setShowForm(false);
                  setEditTarget(null);
                }}
              />
            ) : selectedItem ? (
              <div className="rounded-xl border p-4">
                <div className="mb-4 flex items-center justify-between">
                  <h2 className="text-lg font-semibold">
                    {selectedItem.item_name}
                  </h2>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setEditTarget(selectedItem)}
                    >
                      편집
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10"
                      onClick={() =>
                        selectedItem.id != null &&
                        setDeleteTarget(selectedItem.id)
                      }
                    >
                      삭제
                    </Button>
                  </div>
                </div>
                <dl className="space-y-2 text-sm">
                  {selectedItem.category && (
                    <div>
                      <dt className="text-muted-foreground text-xs">
                        카테고리
                      </dt>
                      <dd className="capitalize">{selectedItem.category}</dd>
                    </div>
                  )}
                  {selectedItem.default_unit_price != null && (
                    <div>
                      <dt className="text-muted-foreground text-xs">
                        기본 단가
                      </dt>
                      <dd>
                        {selectedItem.default_unit_price.toLocaleString(
                          "ko-KR",
                        )}
                        원
                      </dd>
                    </div>
                  )}
                  {selectedItem.notes && (
                    <div>
                      <dt className="text-muted-foreground text-xs">메모</dt>
                      <dd>{selectedItem.notes}</dd>
                    </div>
                  )}
                </dl>
              </div>
            ) : (
              <EmptyState
                icon={PackageIcon}
                title="품목을 선택하세요"
                description="좌측 목록에서 품목을 클릭하세요"
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
            <AlertDialogTitle>품목 삭제</AlertDialogTitle>
            <AlertDialogDescription>
              이 품목을 삭제하시겠습니까?
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

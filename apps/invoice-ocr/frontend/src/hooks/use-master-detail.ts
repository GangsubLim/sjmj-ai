import { useState } from "react";
import { toast } from "sonner";

interface UseMasterDetailOptions {
  deleteFn: (id: number) => Promise<unknown>;
  refetch: () => void;
  deleteSuccessMessage: string;
}

interface UseMasterDetailReturn<T> {
  editTarget: T | null;
  setEditTarget: (value: T | null) => void;
  showForm: boolean;
  setShowForm: (value: boolean) => void;
  deleteTarget: number | null;
  setDeleteTarget: (value: number | null) => void;
  selected: T | null;
  setSelected: (value: T | null) => void;
  handleDelete: (id: number) => Promise<void>;
  handleFormSaved: () => void;
}

export function useMasterDetail<T extends { id?: number }>(
  options: UseMasterDetailOptions,
): UseMasterDetailReturn<T> {
  const { deleteFn, refetch, deleteSuccessMessage } = options;

  const [editTarget, setEditTarget] = useState<T | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [selected, setSelected] = useState<T | null>(null);

  const handleDelete = async (id: number) => {
    try {
      await deleteFn(id);
      toast.success(deleteSuccessMessage);
      refetch();
    } catch {
      toast.error("삭제에 실패했습니다");
    }
  };

  const handleFormSaved = () => {
    setShowForm(false);
    setEditTarget(null);
    refetch();
  };

  return {
    editTarget,
    setEditTarget,
    showForm,
    setShowForm,
    deleteTarget,
    setDeleteTarget,
    selected,
    setSelected,
    handleDelete,
    handleFormSaved,
  };
}

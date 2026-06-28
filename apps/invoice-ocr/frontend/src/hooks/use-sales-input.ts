import { useCallback, useState } from "react";

interface InitialRecord {
  id: number;
  quantity: number;
}

interface Mutations {
  upserts: { salesperson_id: number; quantity: number }[];
  deletes: number[];
}

interface UseSalesInputReturn {
  values: Map<number, string>;
  setValue: (spId: number, raw: string) => void;
  isDirty: (spId: number) => boolean;
  getMutations: () => Mutations;
}

const parseQuantity = (raw: string): number | null => {
  const digits = raw.replace(/[^0-9]/g, "");
  if (digits === "") return null;
  return Number(digits);
};

export function useSalesInput(
  initial: Map<number, InitialRecord>,
  spIds: number[],
): UseSalesInputReturn {
  const [values, setValues] = useState<Map<number, string>>(() => {
    const m = new Map<number, string>();
    for (const id of spIds) {
      const init = initial.get(id);
      m.set(id, init ? String(init.quantity) : "");
    }
    return m;
  });

  const setValue = useCallback((spId: number, raw: string) => {
    setValues((prev) => {
      const next = new Map(prev);
      next.set(spId, raw);
      return next;
    });
  }, []);

  const isDirty = useCallback(
    (spId: number): boolean => {
      const init = initial.get(spId);
      const current = values.get(spId) ?? "";
      const parsed = parseQuantity(current);
      if (init === undefined) {
        return parsed !== null;
      }
      if (current.trim() === "") return true;
      return parsed !== init.quantity;
    },
    [initial, values],
  );

  const getMutations = useCallback((): Mutations => {
    const upserts: Mutations["upserts"] = [];
    const deletes: Mutations["deletes"] = [];
    for (const spId of spIds) {
      const init = initial.get(spId);
      const current = values.get(spId) ?? "";
      const trimmed = current.trim();
      const parsed = parseQuantity(current);

      if (trimmed === "") {
        if (init) deletes.push(init.id);
        continue;
      }
      if (parsed === null) continue;
      if (init && parsed === init.quantity) continue;
      upserts.push({ salesperson_id: spId, quantity: parsed });
    }
    return { upserts, deletes };
  }, [initial, spIds, values]);

  return { values, setValue, isDirty, getMutations };
}

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ui";
import { api, type Params } from "./api";
import { useAuth } from "./auth";
import type { Category } from "./types";

export function useGet<T>(path: string, params?: Params, enabled = true) {
  return useQuery({ queryKey: [path, params ?? {}], queryFn: () => api.get<T>(path, params), enabled });
}

/** Mutation that invalidates every cached query (financial figures are derived from the same transactions). */
export function useAction<TIn, TOut>(fn: (v: TIn) => Promise<TOut>, onDone?: (r: TOut) => void, successMessage?: string | ((r: TOut) => string)) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      void qc.invalidateQueries();
      if (successMessage) toast.success(typeof successMessage === "function" ? successMessage(r) : successMessage);
      onDone?.(r);
    },
  });
}

export function useCurrency(): string {
  return useAuth().user?.currency ?? "INR";
}

export function useCategories() {
  return useGet<{ categories: Category[] }>("/categories");
}

export function currentPeriod(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

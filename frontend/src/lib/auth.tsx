import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setUnauthorizedHandler, tokenStore } from "./api";
import type { User } from "./types";

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (u: User) => void;
};
const Ctx = createContext<AuthState | null>(null);

type TokenResponse = { access_token: string; user: User };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(!!tokenStore.get());
  const qc = useQueryClient();

  const clear = useCallback(() => { tokenStore.clear(); setUser(null); qc.clear(); }, [qc]);

  useEffect(() => {
    setUnauthorizedHandler(clear);
    if (!tokenStore.get()) return;
    api.get<User>("/profile").then(setUser).catch(() => clear()).finally(() => setLoading(false));
  }, [clear]);

  const value = useMemo<AuthState>(() => ({
    user, loading, setUser,
    login: async (email, password) => {
      const r = await api.post<TokenResponse>("/auth/login", { email, password });
      tokenStore.set(r.access_token); qc.clear(); setUser(r.user);
    },
    register: async (name, email, password) => {
      const r = await api.post<TokenResponse>("/auth/register", { name, email, password });
      tokenStore.set(r.access_token); qc.clear(); setUser(r.user);
    },
    logout: async () => {
      try { await api.post("/auth/logout"); } catch { /* token may already be invalid */ }
      clear();
    },
  }), [user, loading, qc, clear]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}

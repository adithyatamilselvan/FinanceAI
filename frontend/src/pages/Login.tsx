import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";
import { IconLogo, IconShield } from "../components/icons";
import { Button, ErrorBox, Field, Input } from "../components/ui";
import { useAuth } from "../lib/auth";

const loginSchema = z.object({ email: z.email("Enter a valid email"), password: z.string().min(1, "Enter your password") });
const registerSchema = z.object({
  name: z.string().trim().min(1, "Enter your name").max(80),
  email: z.email("Enter a valid email"),
  password: z.string().min(8, "At least 8 characters").max(128).refine((v) => !/^\d+$/.test(v) && !/^[A-Za-z]+$/.test(v), "Mix letters with numbers or symbols"),
});
type LoginForm = z.infer<typeof loginSchema>;
type RegisterForm = z.infer<typeof registerSchema>;

export default function Login() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const { login, register } = useAuth();
  const nav = useNavigate();
  const [error, setError] = useState<unknown>(null);

  const lf = useForm<LoginForm>({ resolver: zodResolver(loginSchema) });
  const rf = useForm<RegisterForm>({ resolver: zodResolver(registerSchema) });

  const submitLogin = lf.handleSubmit(async (v) => {
    setError(null);
    try { await login(v.email, v.password); nav("/"); } catch (e) { setError(e); }
  });
  const submitRegister = rf.handleSubmit(async (v) => {
    setError(null);
    try { await register(v.name, v.email, v.password); nav("/"); } catch (e) { setError(e); }
  });

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
    <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-surface p-6 shadow-md sm:p-8">
      <div className="mb-1 flex items-center gap-2.5"><IconLogo /><h1 className="text-2xl font-semibold tracking-tight">FinanceAI</h1></div>
      <p className="mb-6 text-sm text-slate-600">Personal finance tracking, budgeting and planning. Never enter your bank login here; you only upload statement files.</p>
      <div className="mb-5 flex rounded-lg bg-slate-100 p-1 text-sm" role="tablist">
        {(["login", "register"] as const).map((m) => (
          <button key={m} role="tab" aria-selected={mode === m} onClick={() => { setMode(m); setError(null); }}
            className={`flex-1 rounded-md px-3 py-1.5 font-medium transition-colors ${mode === m ? "bg-surface text-blue-700 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}>
            {m === "login" ? "Log in" : "Create account"}
          </button>
        ))}
      </div>
      {error ? <div className="mb-3"><ErrorBox error={error} /></div> : null}
      {mode === "login" ? (
        <form onSubmit={submitLogin} className="space-y-3" noValidate>
          <Field label="Email" error={lf.formState.errors.email?.message}><Input type="email" autoComplete="email" {...lf.register("email")} /></Field>
          <Field label="Password" error={lf.formState.errors.password?.message}><Input type="password" autoComplete="current-password" {...lf.register("password")} /></Field>
          <Button type="submit" className="w-full" disabled={lf.formState.isSubmitting}>{lf.formState.isSubmitting ? "Logging in…" : "Log in"}</Button>
        </form>
      ) : (
        <form onSubmit={submitRegister} className="space-y-3" noValidate>
          <Field label="Name" error={rf.formState.errors.name?.message}><Input autoComplete="name" {...rf.register("name")} /></Field>
          <Field label="Email" error={rf.formState.errors.email?.message}><Input type="email" autoComplete="email" {...rf.register("email")} /></Field>
          <Field label="Password" error={rf.formState.errors.password?.message} hint="At least 8 characters, mixing letters and numbers or symbols.">
            <Input type="password" autoComplete="new-password" {...rf.register("password")} />
          </Field>
          <Button type="submit" className="w-full" disabled={rf.formState.isSubmitting}>{rf.formState.isSubmitting ? "Creating account…" : "Create account"}</Button>
        </form>
      )}
      <p className="mt-5 flex items-start gap-2 text-xs text-slate-500"><IconShield className="mt-0.5 h-4 w-4 shrink-0 text-green-700" />Passwords are stored as Argon2id hashes. Your data stays in your own database.</p>
    </div>
    </main>
  );
}

import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { useAuth } from "./lib/auth";
import Login from "./pages/Login";

// Route-level code splitting keeps the first load small (Recharts is only fetched by pages that draw charts).
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Transactions = lazy(() => import("./pages/Transactions"));
const ImportPage = lazy(() => import("./pages/Import"));
const Slips = lazy(() => import("./pages/Slips"));
const Budgets = lazy(() => import("./pages/Budgets"));
const Goals = lazy(() => import("./pages/Goals"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Optimize = lazy(() => import("./pages/Optimize"));
const Reports = lazy(() => import("./pages/Reports"));
const Settings = lazy(() => import("./pages/Settings"));

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <Spinner label="Checking your session…" />;
  if (!user) return <Navigate to="/login" replace />;
  return <Layout><Suspense fallback={<Spinner />}><Outlet /></Suspense></Layout>;
}

export default function App() {
  const { user, loading } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={loading ? <Spinner /> : user ? <Navigate to="/" replace /> : <Login />} />
      <Route element={<Protected />}>
        <Route index element={<Dashboard />} />
        <Route path="transactions" element={<Transactions />} />
        <Route path="import" element={<ImportPage />} />
        <Route path="slips" element={<Slips />} />
        <Route path="budgets" element={<Budgets />} />
        <Route path="goals" element={<Goals />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="optimize" element={<Optimize />} />
        <Route path="reports" element={<Reports />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

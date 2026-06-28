/* eslint-disable react-refresh/only-export-components -- entry 파일: lazy route 정의는 HMR 컴포넌트 모듈이 아님 */
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "@/styles/globals.css";

import { AppShell } from "@/components/layout";
import { ErrorFallback } from "@/components/layout/ErrorFallback";
import InvoiceCreatePage from "@/app/page";
import InvoiceEditPage from "@/app/edit/page";
import InvoiceListPage from "@/app/list/page";

const CompanyManagePage = lazy(() => import("@/app/companies/page"));
const ItemManagePage = lazy(() => import("@/app/items/page"));
const SettingsPage = lazy(() => import("@/app/settings/page"));
const SalesPerformancePage = lazy(
  () => import("@/app/sales-performance/page"),
);

const LazyFallback = (
  <div className="flex min-h-dvh items-center justify-center text-sm text-gray-400">
    로딩 중...
  </div>
);

const router = createBrowserRouter([
  {
    element: <AppShell />,
    errorElement: <ErrorFallback />,
    children: [
      { path: "/", element: <InvoiceCreatePage /> },
      { path: "/edit/:id", element: <InvoiceEditPage /> },
      { path: "/list", element: <InvoiceListPage /> },
      {
        path: "/companies",
        element: (
          <Suspense fallback={LazyFallback}>
            <CompanyManagePage />
          </Suspense>
        ),
      },
      {
        path: "/items",
        element: (
          <Suspense fallback={LazyFallback}>
            <ItemManagePage />
          </Suspense>
        ),
      },
      {
        path: "/settings",
        element: (
          <Suspense fallback={LazyFallback}>
            <SettingsPage />
          </Suspense>
        ),
      },
      {
        path: "/sales-performance",
        element: (
          <Suspense fallback={LazyFallback}>
            <SalesPerformancePage />
          </Suspense>
        ),
      },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);

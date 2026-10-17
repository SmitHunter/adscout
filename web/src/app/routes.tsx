import { createBrowserRouter, type RouteObject } from "react-router";

import { HomePage } from "@/features/search/HomePage";
import { NotFoundPage } from "@/pages/NotFoundPage";

import { Shell } from "./Shell";

export const routes: RouteObject[] = [
  {
    // Outside the shell: no rail. The PDF and PNG exports render this page.
    path: "/report/:id/print",
    lazy: () => import("@/features/report/PrintReport").then((m) => ({ Component: m.PrintReport })),
  },
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <HomePage /> },
      {
        path: "report/:id",
        // Split out: the report pulls in the chart library.
        lazy: () =>
          import("@/features/report/ReportPage").then((m) => ({ Component: m.ReportPage })),
      },
      {
        path: "compare",
        lazy: () =>
          import("@/features/compare/ComparePage").then((m) => ({ Component: m.ComparePage })),
      },
      {
        path: "watch",
        lazy: () =>
          import("@/features/watch/WatchlistPage").then((m) => ({ Component: m.WatchlistPage })),
      },
      {
        path: "watch/:id",
        lazy: () =>
          import("@/features/watch/WatchItemPage").then((m) => ({ Component: m.WatchItemPage })),
      },
      {
        path: "settings",
        lazy: () =>
          import("@/features/settings/SettingsPage").then((m) => ({ Component: m.SettingsPage })),
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}

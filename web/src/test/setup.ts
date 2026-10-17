import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { toast } from "sonner";
import { afterEach } from "vitest";

afterEach(() => {
  // Sonner's toast store is global: dismiss so one test's toast never shows in the next.
  toast.dismiss();
  cleanup();
});

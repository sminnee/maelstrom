import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Mock @tauri-apps/api/core for unit tests
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

// Mock ResizeObserver for jsdom (used by Radix UI ScrollArea)
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
};

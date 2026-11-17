// @vitest-environment node
import { afterEach, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";

const ctx = vi.hoisted(() => ({
  ipc: new Map<string, (...args: unknown[]) => unknown>(),
  check: vi.fn(async () => ({ updateInfo: { version: "99.0.0" }, isUpdateAvailable: true })),
  download: vi.fn(),
  install: vi.fn(),
  userData: "",
}));

vi.mock("electron", () => ({
  app: {
    getVersion: () => "0.9.0-local",
    getPath: () => ctx.userData,
    on: vi.fn(),
    quit: vi.fn(),
  },
  ipcMain: {
    handle: (channel: string, handler: (...args: unknown[]) => unknown) => {
      ctx.ipc.set(channel, handler);
    },
  },
}));

vi.mock("electron-updater", () => ({
  autoUpdater: {
    on: vi.fn(),
    checkForUpdates: ctx.check,
    downloadUpdate: ctx.download,
    quitAndInstall: ctx.install,
    autoDownload: true,
    autoInstallOnAppQuit: true,
  },
}));

import { autoUpdater } from "electron-updater";
import { setupAutoUpdater } from "./updater";

let userData: string | undefined;

afterEach(() => {
  vi.useRealTimers();
  if (userData) {
    // Only remove the directory created by this test, never Electron userData.
    if (resolve(dirname(userData)) !== resolve(tmpdir()) || !basename(userData).startsWith("multica-local-updater-")) {
      throw new Error("Unexpected test directory");
    }
    rmSync(userData, { recursive: true, force: true });
  }
});

it("keeps Local builds off the production update feed through every updater entry point", async () => {
  userData = mkdtempSync(join(tmpdir(), "multica-local-updater-"));
  ctx.userData = userData;
  vi.useFakeTimers();
  setupAutoUpdater(() => [], { disabled: true });

  await ctx.ipc.get("updater:check")?.();
  expect(ctx.check).not.toHaveBeenCalled();
  expect(await ctx.ipc.get("updater:set-automatic-updates")?.(undefined, true)).toEqual({ automaticUpdates: false });
  expect(await ctx.ipc.get("updater:get-preferences")?.()).toEqual({ automaticUpdates: false });
  await ctx.ipc.get("updater:download")?.();
  await ctx.ipc.get("updater:install")?.();
  await vi.advanceTimersByTimeAsync(2 * 60 * 60 * 1000);

  expect(ctx.check).not.toHaveBeenCalled();
  expect(ctx.download).not.toHaveBeenCalled();
  expect(ctx.install).not.toHaveBeenCalled();
  expect(autoUpdater.autoDownload).toBe(false);
  expect(autoUpdater.autoInstallOnAppQuit).toBe(false);
});

import { describe, expect, it, vi } from "vitest";

/**
 * Desktop host lifecycle (SDD §1 I3/I4/I5, §4).
 *
 * These tests exist because the failure modes are silent: a rebuilt view loses
 * page state, a throttled view stops animating, and a second view on the same
 * partition is a second Chromium. None of them throw.
 *
 * `electron` is mocked because the module under test constructs the real
 * classes only in its production adapter; the tests inject fake adapters and
 * the alias in vitest.config.ts would otherwise throw on import.
 */

const removeMock = vi.hoisted(() => ({ calls: [] as string[], fail: false }));

vi.mock("electron", () => ({
  BaseWindow: class {},
  WebContentsView: class {},
}));

import { QaBrowserHost, readURLParts } from "./qa-browser-host";
import type { QaBrowserHostAdapters } from "./qa-browser-host";

/**
 * Vitest's `Mock` type is not assignable to a precise function signature, so
 * the fakes are asserted once at this boundary rather than loosened inside the
 * host module. The assertion covers only vitest's own generic wrapper.
 */
const asAdapters = (adapters: unknown) => adapters as QaBrowserHostAdapters;

type Listener = (...args: unknown[]) => void;

interface FakeView {
  webContents: {
    id: number;
    loadURL: ReturnType<typeof vi.fn>;
    getURL: () => string;
    isDestroyed: () => boolean;
    getZoomFactor: () => number;
    capturePage: ReturnType<typeof vi.fn>;
    on: (event: string, listener: Listener) => unknown;
    setWindowOpenHandler: ReturnType<typeof vi.fn>;
    session: { setPermissionRequestHandler: ReturnType<typeof vi.fn>; on: ReturnType<typeof vi.fn> };
    debugger: {
      attach: ReturnType<typeof vi.fn>;
      isAttached: () => boolean;
      sendCommand: ReturnType<typeof vi.fn>;
      on: ReturnType<typeof vi.fn>;
      removeListener: ReturnType<typeof vi.fn>;
    };
  };
  setBounds: ReturnType<typeof vi.fn>;
  setVisible: ReturnType<typeof vi.fn>;
}

function fakeView(id = 1): FakeView {
  const sendCommand = vi.fn(async (method: string) => {
    if (method === "Accessibility.getFullAXTree") {
      return {
        nodes: [
          { role: { value: "button" }, name: { value: "increment" } },
          { role: { value: "textbox" }, name: { value: "query" } },
          { role: { value: "none" }, name: { value: "" } },
        ],
      };
    }
    if (method === "Runtime.evaluate") return { result: { value: { ok: true, x: 10, y: 20 } } };
    return {};
  });
  return {
    webContents: {
      id,
      loadURL: vi.fn(async () => undefined),
      getURL: () => "http://127.0.0.1:3000/dashboard?token=secret",
      isDestroyed: () => false,
      getZoomFactor: () => 1,
      capturePage: vi.fn(async () => ({
        toPNG: () => Buffer.from("png"),
        isEmpty: () => false,
      })),
      on: () => undefined,
      setWindowOpenHandler: vi.fn(),
      session: { setPermissionRequestHandler: vi.fn(), on: vi.fn() },
      debugger: {
        attach: vi.fn(),
        isAttached: () => true,
        sendCommand,
        on: vi.fn(),
        removeListener: vi.fn(),
      },
    },
    setBounds: vi.fn(),
    setVisible: vi.fn(),
  };
}

interface FakeWindow {
  contentView: { addChildView: ReturnType<typeof vi.fn>; removeChildView: ReturnType<typeof vi.fn> };
  getContentBounds: () => { x: number; y: number; width: number; height: number };
  isMinimized: () => boolean;
  show: ReturnType<typeof vi.fn>;
  focus: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
  isDestroyed: () => boolean;
  webContents: { id: number };
}

function fakeWindow(overrides: Partial<FakeWindow> = {}): FakeWindow {
  return {
    contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
    getContentBounds: () => ({ x: 0, y: 0, width: 1200, height: 900 }),
    isMinimized: () => false,
    show: vi.fn(),
    focus: vi.fn(),
    destroy: vi.fn(),
    isDestroyed: () => false,
    webContents: { id: 9 },
    ...overrides,
  };
}

function makeHost(options: { sendCommand?: (method: string) => Record<string, unknown> } = {}) {
  const views: FakeView[] = [];
  const windows: FakeWindow[] = [];
  const adapters = {
    createWindow: () => {
      const window = fakeWindow();
      windows.push(window);
      return window;
    },
    createView: () => {
      const view = fakeView(views.length + 1);
      if (options.sendCommand) view.webContents.debugger.sendCommand.mockImplementation(async (method: string) => options.sendCommand!(method));
      views.push(view);
      return view;
    },
    partitionDirFor: (_sessionId: string, partition: string) => `/tmp/multica-qa-browser/${partition}`,
    removePartitionDir: (dir: string) => {
      if (removeMock.fail) throw new Error("EBUSY");
      removeMock.calls.push(dir);
    },
  };
  return { adapters, views, windows, host: new QaBrowserHost(asAdapters(adapters)) };
}

const SESSION = { sessionId: "bs_1", taskId: "t1", issueId: "i1", partition: "qa-task-t1" };

describe("QaBrowserHost", () => {
  it("creates one view per session and never a second for a repeat call", () => {
    const { views, windows, host } = makeHost();

    const first = host.ensure(SESSION);
    const second = host.ensure(SESSION);

    expect(views).toHaveLength(1);
    expect(windows).toHaveLength(1);
    expect(second.view.webContents.id).toBe(first.view.webContents.id);
  });

  it("never shows the window on creation, only when raised", () => {
    const { windows, host } = makeHost();
    host.ensure(SESSION);
    // P3: an agent start must not steal focus.
    expect(windows[0]!.show).not.toHaveBeenCalled();

    expect(host.raise("bs_1")).toBe(true);
    expect(windows[0]!.show).toHaveBeenCalledTimes(1);
    expect(windows[0]!.focus).toHaveBeenCalledTimes(1);
  });

  it("toggles visibility without rebuilding the view", () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);

    host.setVisible("bs_1", false);
    host.setVisible("bs_1", true);

    expect(views).toHaveLength(1);
    expect(views[0]!.setVisible).toHaveBeenNthCalledWith(1, false);
    expect(views[0]!.setVisible).toHaveBeenNthCalledWith(2, true);
  });

  it("refuses permissions and redirects window.open into the same view", () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);
    const webContents = views[0]!.webContents;

    expect(webContents.session.setPermissionRequestHandler).toHaveBeenCalledTimes(1);
    const permissionHandler = webContents.session.setPermissionRequestHandler.mock.calls[0]![0] as (
      contents: unknown,
      permission: string,
      callback: (granted: boolean) => void,
    ) => void;
    const granted = vi.fn();
    permissionHandler(null, "geolocation", granted);
    expect(granted).toHaveBeenCalledWith(false);

    const openHandler = webContents.setWindowOpenHandler.mock.calls[0]![0] as (details: {
      url: string;
    }) => { action: string };
    expect(openHandler({ url: "https://example.com/x" }).action).toBe("deny");
    // target=_blank navigates in place rather than reaching the OS shell.
    expect(webContents.loadURL).toHaveBeenCalledWith("https://example.com/x");
  });

  it("captures only through capturePage and enforces the 4 MiB artifact cap", async () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);

    const captured = await host.capture("bs_1", 4 << 20);
    expect(views[0]!.webContents.capturePage).toHaveBeenCalledTimes(1);
    expect(captured && "png" in captured).toBe(true);

    expect(await host.capture("bs_1", 1)).toEqual({ tooLarge: 3 });
  });

  it("destroys the view before deleting its partition directory", () => {
    const { windows, host } = makeHost();
    host.ensure(SESSION);

    const result = host.destroy("bs_1");
    expect(result).toEqual({ deleted: true, profileDir: "/tmp/multica-qa-browser/qa-task-t1" });
    expect(windows[0]!.destroy).toHaveBeenCalledTimes(1);
    expect(removeMock.calls).toEqual(["/tmp/multica-qa-browser/qa-task-t1"]);
  });

  it("reports a failed directory delete instead of claiming cleanup succeeded", () => {
    removeMock.fail = true;
    try {
      const { host } = makeHost();
      host.ensure(SESSION);
      expect(host.destroy("bs_1")).toEqual({
        deleted: false,
        profileDir: "/tmp/multica-qa-browser/qa-task-t1",
      });
    } finally {
      removeMock.fail = false;
    }
  });

  it("skips a bounds update while the window is minimised", () => {
    const views: FakeView[] = [];
    const host = new QaBrowserHost(
      asAdapters({
        createWindow: () => fakeWindow({ isMinimized: () => true }),
        createView: () => {
          const view = fakeView(1);
          views.push(view);
          return view;
        },
        partitionDirFor: () => "/tmp/qa-task-t1",
        removePartitionDir: () => undefined,
      }),
    );
    host.ensure(SESSION);

    expect(host.setBounds("bs_1", { x: 0, y: 0, width: 100, height: 100 }, 1)).toBe(true);
    expect(views[0]!.setBounds).not.toHaveBeenCalled();
  });

  it("executes a snapshot as a flat ref list, dropping unnamed noise", async () => {
    const { host } = makeHost();

    host.ensure(SESSION);
    const result = await host.execute("bs_1", { id: "c1", action: "browser_snapshot" });
    expect(result.ok).toBe(true);
    const nodes = (result.result as { nodes: { ref: string; role: string }[] }).nodes;
    expect(nodes).toHaveLength(2);
    expect(nodes[0]).toEqual({ ref: "e1", role: "button", name: "increment" });
  });

  it("reports BROWSER_STALE_REF when a ref no longer resolves", async () => {
    const { host } = makeHost({
      sendCommand: (method) =>
        method === "Runtime.evaluate"
          ? { result: { value: { ok: false, error: "BROWSER_STALE_REF" } } }
          : {},
    });
    host.ensure(SESSION);

    const result = await host.execute("bs_1", { id: "c1", action: "browser_click", params: { ref: "e9" } });
    expect(result.ok).toBe(false);
    expect(result.error).toBe("BROWSER_STALE_REF");
  });

  it("refuses a non-http goto rather than handing the view a file URL", async () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);

    const result = await host.execute("bs_1", {
      id: "c1",
      action: "browser_goto",
      params: { url: "file:///etc/passwd" },
    });
    expect(result.ok).toBe(false);
    expect(views[0]!.webContents.loadURL).not.toHaveBeenCalled();
  });

  it("answers BROWSER_CAPABILITY_UNSUPPORTED for an unknown session", async () => {
    const { host } = makeHost();
    const result = await host.execute("bs_missing", { id: "c1", action: "browser_url" });
    expect(result).toEqual({ id: "c1", ok: false, error: "BROWSER_CAPABILITY_UNSUPPORTED" });
  });

  it("drops the query string from a reported URL", () => {
    expect(readURLParts("http://127.0.0.1:3000/dashboard?token=secret#x")).toEqual({
      host: "127.0.0.1:3000",
      path: "/dashboard",
    });
  });
});

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
    /** Test hook: model a window the user closed. */
    __destroy: () => void;
    loadURL: ReturnType<typeof vi.fn>;
    getURL: () => string;
    isDestroyed: () => boolean;
    getZoomFactor: () => number;
    capturePage: ReturnType<typeof vi.fn>;
    on: (event: string, listener: Listener) => unknown;
    /** Listeners the host registered, keyed by event name. */
    __listeners: Record<string, Listener[]>;
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
  const listeners: Record<string, Listener[]> = {};
  let attached = false;
  let destroyed = false;
  return {
    webContents: {
      id,
      __listeners: listeners,
      // Tests flip this to model a window the user closed.
      __destroy: () => {
        destroyed = true;
      },
      loadURL: vi.fn(async () => undefined),
      getURL: () => "http://127.0.0.1:3000/dashboard?token=secret",
      isDestroyed: () => destroyed,
      getZoomFactor: () => 1,
      capturePage: vi.fn(async () => ({
        toPNG: () => Buffer.from("png"),
        isEmpty: () => false,
      })),
      on: (event: string, listener: Listener) => {
        listeners[event] = [...(listeners[event] ?? []), listener];
        return undefined;
      },
      setWindowOpenHandler: vi.fn(),
      session: { setPermissionRequestHandler: vi.fn(), on: vi.fn() },
      debugger: {
        attach: vi.fn(() => {
          attached = true;
        }),
        // Starts unattached, like a fresh view: `ensureDebugger` only wires the
        // console listener on the attach path, so a fake that claims to be
        // already attached silently skips it.
        isAttached: () => attached,
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
  showInactive: ReturnType<typeof vi.fn>;
  setPosition: ReturnType<typeof vi.fn>;
  getBounds: () => { x: number; y: number; width: number; height: number };
  focus: ReturnType<typeof vi.fn>;
  destroy: ReturnType<typeof vi.fn>;
  isDestroyed: () => boolean;
  on: (event: string, listener: () => void) => unknown;
  __close: () => void;
  webContents: { id: number };
}

const WORK_AREA = { x: 0, y: 0, width: 1920, height: 1032 };

function fakeWindow(overrides: Partial<FakeWindow> = {}): FakeWindow {
  let pos = { x: -20000, y: -20000 };
  const closedListeners: (() => void)[] = [];
  return {
    contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
    getContentBounds: () => ({ x: 0, y: 0, width: 1200, height: 900 }),
    isMinimized: () => false,
    show: vi.fn(),
    showInactive: vi.fn(),
    setPosition: vi.fn((x: number, y: number) => {
      pos = { x, y };
    }),
    getBounds: () => ({ x: pos.x, y: pos.y, width: 1000, height: 720 }),
    focus: vi.fn(),
    destroy: vi.fn(),
    isDestroyed: () => false,
    // The user closing the QA window fires this; the host must drop its entry.
    on: (event: string, listener: () => void) => {
      if (event === "closed") closedListeners.push(listener);
      return undefined;
    },
    __close: () => {
      for (const listener of closedListeners) listener();
    },
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
    workArea: () => WORK_AREA,
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

  it("takes a capture surface off-screen without stealing focus (P3 + I5)", () => {
    const created: unknown[] = [];
    const windows: FakeWindow[] = [];
    const host = new QaBrowserHost(
      asAdapters({
        createWindow: (options: unknown) => {
          created.push(options);
          const window = fakeWindow();
          windows.push(window);
          return window;
        },
        createView: () => fakeView(1),
      }),
    );
    host.ensure(SESSION);

    // Measured on Electron 44.9.3: a never-shown window cannot capture at all
    // ("Current display surface not available"), so creation must produce a
    // surface — off-screen, without activation, and off the taskbar.
    expect(created[0]).toMatchObject({ show: false, x: -20000, y: -20000, skipTaskbar: true });
    expect(windows[0]!.showInactive).toHaveBeenCalledTimes(1);
    // P3 still holds: nothing activates and nothing is raised.
    expect(windows[0]!.show).not.toHaveBeenCalled();
    expect(windows[0]!.focus).not.toHaveBeenCalled();
  });

  it("raises the window only on an explicit takeover", () => {
    const { windows, host } = makeHost();
    host.ensure(SESSION);
    expect(windows[0]!.show).not.toHaveBeenCalled();

    expect(host.raise("bs_1")).toBe(true);
    expect(windows[0]!.show).toHaveBeenCalledTimes(1);
    expect(windows[0]!.focus).toHaveBeenCalledTimes(1);
  });

  it("brings the raised window inside the work area, and keeps the capture surface", async () => {
    const { views, windows, host } = makeHost();
    host.ensure(SESSION);
    // Parked off-screen at creation (that is what buys a surface silently).
    expect(windows[0]!.getBounds().x).toBe(-20000);

    host.raise("bs_1");

    const bounds = windows[0]!.getBounds();
    expect(bounds.x).toBeGreaterThanOrEqual(WORK_AREA.x);
    expect(bounds.y).toBeGreaterThanOrEqual(WORK_AREA.y);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(WORK_AREA.x + WORK_AREA.width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(WORK_AREA.y + WORK_AREA.height);

    // Visibility must not cost the screenshot chain: measured on Electron
    // 44.9.3, moving while visible keeps the display surface; moving before the
    // first showInactive does not.
    const capture = await host.capture("bs_1", 4 << 20);
    expect(capture && "png" in capture).toBe(true);
    expect(views[0]!.webContents.capturePage).toHaveBeenCalled();
  });

  it("also brings the window on screen when the sidebar shows it", () => {
    const { windows, host } = makeHost();
    host.ensure(SESSION);
    host.setVisible("bs_1", true);
    expect(windows[0]!.getBounds().x).toBeGreaterThanOrEqual(0);
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

  it("destroys the view and the window, and touches no filesystem path", () => {
    const { windows, host } = makeHost();
    host.ensure(SESSION);

    expect(host.destroy("bs_1")).toBe(true);
    expect(windows[0]!.destroy).toHaveBeenCalledTimes(1);
    expect(windows[0]!.contentView.removeChildView).toHaveBeenCalledTimes(1);
    expect(host.has("bs_1")).toBe(false);
    // A6 is "view destroyed + registry cleared", not "directory deleted": the
    // partition is non-persistent, so there is no directory to delete.
  });

  it("reports a second destroy as a no-op rather than a success", () => {
    const { host } = makeHost();
    host.ensure(SESSION);
    expect(host.destroy("bs_1")).toBe(true);
    expect(host.destroy("bs_1")).toBe(false);
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
      }),
    );
    host.ensure(SESSION);

    // Creation installs one baseline rectangle; the minimised update must add
    // nothing on top of it.
    const callsAfterCreate = views[0]!.setBounds.mock.calls.length;
    expect(host.setBounds("bs_1", { x: 0, y: 0, width: 100, height: 100 }, 1)).toBe(true);
    expect(views[0]!.setBounds.mock.calls.length).toBe(callsAfterCreate);
  });

  it("gives the view a real rectangle at creation, so an early capture works", () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);
    // A freshly added WebContentsView is 0x0 until a bounds message arrives, and
    // a 0x0 view captures an empty image — which would fail browser_screenshot
    // on any session faster than the renderer's first geometry message.
    expect(views[0]!.setBounds).toHaveBeenCalledWith({ x: 0, y: 0, width: 1200, height: 900 });
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

  it("blocks a page-initiated navigation to file:, data: and javascript:", () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);
    const webContents = views[0]!.webContents;
    const listeners = webContents.__listeners ?? {};
    // Both hooks must exist: setWindowOpenHandler only covers window.open.
    expect(Object.keys(listeners)).toEqual(expect.arrayContaining(["will-navigate", "will-redirect"]));

    for (const channel of ["will-navigate", "will-redirect"]) {
      for (const blocked of ["file:///etc/passwd", "data:text/html,<script>alert(1)</script>", "javascript:alert(1)"]) {
        const preventDefault = vi.fn();
        listeners[channel]!.forEach((listener) => listener({ preventDefault }, blocked));
        expect(preventDefault).toHaveBeenCalled();
      }
      const preventDefault = vi.fn();
      listeners[channel]!.forEach((listener) => listener({ preventDefault }, "https://example.com/ok"));
      expect(preventDefault).not.toHaveBeenCalled();
    }
  });

  it("attaches a screenshot through capturePage when the agent asks for one", async () => {
    const { views, host } = makeHost();
    host.ensure(SESSION);
    const sender = vi.fn(async () => ({ ok: true as const, attachmentId: "att_shot" }));
    host.setEvidenceSender(sender);

    const result = await host.execute("bs_1", { id: "c1", action: "browser_screenshot" });

    expect(views[0]!.webContents.capturePage).toHaveBeenCalledTimes(1);
    expect(sender).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(true);
    expect(result.result).toMatchObject({ attachment_id: "att_shot", viewport_only: true });
    expect(result.text).toContain("att_shot");
  });

  it("fails browser_screenshot without uploading when the PNG exceeds 4 MiB", async () => {
    const { host } = makeHost();
    host.ensure(SESSION);
    const sender = vi.fn(async () => ({ ok: true as const, attachmentId: "att_shot" }));
    host.setEvidenceSender(sender, 1);

    const result = await host.execute("bs_1", { id: "c1", action: "browser_screenshot" });

    expect(result.ok).toBe(false);
    expect(result.error).toBe("BROWSER_EVIDENCE_FAILED");
    // The daemon would accept 4 MiB + 1 KiB, so the refusal must happen here.
    expect(sender).not.toHaveBeenCalled();
  });

  it("reports an evidence failure instead of a vague session error", async () => {
    const { host } = makeHost();
    host.ensure(SESSION);
    host.setEvidenceSender(async () => ({ ok: false as const, reason: "rejected" }));

    const result = await host.execute("bs_1", { id: "c1", action: "browser_screenshot" });
    expect(result).toEqual({ id: "c1", ok: false, error: "BROWSER_EVIDENCE_FAILED" });
  });

  it("redacts console output before returning it", async () => {
    // Seed the buffer through the real listener path: `execute` attaches the
    // debugger, which registers the console listener, so the line has to be
    // delivered the way Chromium delivers it.
    const emitted: string[] = [];
    const { views, host } = makeHost();
    host.ensure(SESSION);
    const webContents = views[0]!.webContents;
    const debuggerMock = webContents.debugger as unknown as {
      on: (event: string, listener: (...args: unknown[]) => void) => void;
    };
    const originalOn = debuggerMock.on;
    debuggerMock.on = (event, listener) => {
      originalOn.call(debuggerMock, event, listener);
      if (event === "message") {
        emitted.push("registered");
        // Chromium's shape: (event, method, params, sessionId).
        listener({}, "Runtime.consoleAPICalled", {
          args: [{ value: "Cookie: session=abc123def456" }, { value: "sid=SECRETVALUE0123456789" }],
        }, "");
      }
    };

    const result = await host.execute("bs_1", { id: "c1", action: "browser_console" });
    expect(emitted).toHaveLength(1);
    expect(result.ok).toBe(true);
    expect(result.text).not.toContain("abc123def456");
    expect(result.text).not.toContain("SECRETVALUE0123456789");
    expect(result.text).toContain("[REDACTED COOKIE]");
  });

  it("rebuilds instead of reusing an entry whose view was destroyed", () => {
    // P1-10: the user closed the QA window. `ensure` used to return the cached
    // entry, so "open browser" handed back a dead view forever while the daemon
    // saw view_alive=false on every heartbeat.
    const { views, windows, host } = makeHost();
    const first = host.ensure(SESSION);
    (first.view.webContents as unknown as { __destroy: () => void }).__destroy();

    const second = host.ensure(SESSION);

    expect(views).toHaveLength(2);
    expect(windows).toHaveLength(2);
    expect(second.view.webContents.id).not.toBe(first.view.webContents.id);
    expect(windows[0]!.destroy).toHaveBeenCalled();
  });

  it("drops its entry when the user closes the QA window", () => {
    // P1-10: the QA window's own close path is not one of the Multica window
    // handlers, so without this listener the entry outlived its window.
    const { windows, host } = makeHost();
    host.ensure(SESSION);
    expect(host.has("bs_1")).toBe(true);

    windows[0]!.__close();

    expect(host.has("bs_1")).toBe(false);
  });

  it("drops the page's last value from a wait timeout", async () => {
    // P2-11: the failing predicate's value is page data with no diagnostic
    // value, and `Boolean(...)` only kept it harmless by accident.
    const { host } = makeHost({ sendCommand: (method) => method === "Runtime.evaluate" ? { result: { value: false } } : {} });
    host.ensure(SESSION);
    const result = await host.execute("bs_1", {
      id: "c1",
      action: "browser_wait",
      params: { condition: "document.title === 'x'", timeout_ms: 1 },
    });
    expect(result.ok).toBe(false);
    expect(result.error).toBe("condition not met before the timeout");
    expect(result.error).not.toContain("last=");
  });

  it("drops the query string from a reported URL", () => {
    expect(readURLParts("http://127.0.0.1:3000/dashboard?token=secret#x")).toEqual({
      host: "127.0.0.1:3000",
      path: "/dashboard",
    });
  });
});

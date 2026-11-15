import { BaseWindow, WebContentsView } from "electron";
import { join } from "path";
import { tmpdir } from "os";
import { mkdirSync, rmSync } from "fs";
import { computeViewBounds } from "../shared/qa-browser";
import type { QaBrowserRect } from "../shared/qa-browser";
import type { QaBrowserHostCommand, QaBrowserHostCommandResult } from "./qa-browser-client";

/**
 * QA built-in isolated browser: the visible view (SDD §4, §6).
 *
 * One `BaseWindow` + one `WebContentsView` per session, created once and kept
 * for the session's whole life. Nothing here is driven by React: `tab-content`
 * mounts one tab at a time and unmounts the subtree on every switch, so a
 * component-driven lifecycle would reload the QA page on each tab change and
 * break invariant I3.
 *
 * Security posture differs from the app's own renderer on purpose. The main
 * renderer runs `webSecurity: false` + `plugins: true`; the QA view must never
 * reuse `createRendererWebPreferences`.
 */

/** The QA view's own preferences — never the renderer's. */
export const QA_BROWSER_WEB_PREFERENCES = {
  // Per-task, non-persistent partition: process exit leaves no cookie behind.
  // The name carries the task id, which is what stops cross-task reuse (I2).
  sandbox: true,
  contextIsolation: true,
  nodeIntegration: false,
  webSecurity: true,
  // Hard requirement (I4): a hidden window throttles rAF to zero, which
  // silently breaks every agent wait condition and lazy load in the page.
  backgroundThrottling: false,
} as const;

/** The minimal view surface this module drives; keeps the manager testable. */
export type QaBrowserView = {
  webContents: {
    id: number;
    loadURL(url: string): Promise<void> | void;
    getURL(): string;
    isDestroyed(): boolean;
    getZoomFactor(): number;
    capturePage(): Promise<{ toPNG(): Buffer; isEmpty(): boolean }>;
    on(event: string, listener: (...args: unknown[]) => void): unknown;
    setWindowOpenHandler(handler: (details: { url: string }) => { action: "deny" }): void;
    /** In-process CDP. A process-level --remote-debugging-port is forbidden (I1). */
    debugger: {
      attach(protocolVersion: string): void;
      isAttached(): boolean;
      detach(): void;
      sendCommand(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
      on(event: "message", listener: (event: unknown, method: string, params: unknown, sessionId: string) => void): unknown;
      removeListener(event: "message", listener: (event: unknown, method: string, params: unknown, sessionId: string) => void): unknown;
    };
    session: {
      setPermissionRequestHandler(handler: (contents: unknown, permission: string, callback: (granted: boolean) => void) => void): void;
      on(event: string, listener: (...args: unknown[]) => void): unknown;
    };
  };
  setBounds(bounds: { x: number; y: number; width: number; height: number }): void;
  setVisible(visible: boolean): void;
};

export type QaBrowserWindow = {
  contentView: { addChildView(view: QaBrowserView): void; removeChildView(view: QaBrowserView): void };
  getContentBounds(): { x: number; y: number; width: number; height: number };
  isMinimized(): boolean;
  show(): void;
  focus(): void;
  destroy(): void;
  isDestroyed(): boolean;
  webContents: { id: number };
};

export type QaBrowserHostAdapters = {
  createWindow(options: { title: string; width: number; height: number; show: boolean }): QaBrowserWindow;
  createView(options: { partition: string }): QaBrowserView;
  /** Where a session's partition is materialised on disk (deleted on cleanup). */
  partitionDirFor(sessionId: string, partition: string): string;
  /**
   * Deletes that directory. Its own adapter because the ordering against view
   * destruction is the invariant under test: on Windows a live renderer holds
   * file locks, so deleting first always fails.
   */
  removePartitionDir(dir: string): void;
};

export type QaBrowserHostEntry = {
  sessionId: string;
  taskId: string;
  issueId: string;
  partition: string;
  profileDir: string;
  window: QaBrowserWindow;
  view: QaBrowserView;
  visible: boolean;
  /** Strictly increasing heartbeat sequence; a replayed value is dropped. */
  seq: number;
  /** Set once the daemon asked for the view to be destroyed. */
  closing: boolean;
  /** Commands executed by the view, keyed for result correlation. */
  executed: number;
  /** Console lines seen since attach; already bounded, redacted by the daemon. */
  console: string[];
  consoleListener?: (event: unknown, method: string, params: unknown, sessionId: string) => void;
};

export type QaBrowserExecuteResult = QaBrowserHostCommandResult;

/** Creates the window pairing used in production. */
export function createProductionAdapters(): QaBrowserHostAdapters {
  return {
    createWindow: (options) =>
      new BaseWindow({
        title: options.title,
        width: options.width,
        height: options.height,
        show: options.show,
      }) as unknown as QaBrowserWindow,
    createView: (options) =>
      new WebContentsView({
        webPreferences: {
          ...QA_BROWSER_WEB_PREFERENCES,
          partition: options.partition,
        },
      }) as unknown as QaBrowserView,
    partitionDirFor: (_sessionId, partition) => {
      // The daemon deletes this exact path, and validates that its leaf is the
      // session's partition, so a wrong name is refused rather than deleting
      // something unrelated.
      const root = join(tmpdir(), "multica-qa-browser");
      mkdirSync(root, { recursive: true });
      return join(root, partition);
    },
    removePartitionDir: (dir) => {
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

/**
 * Owns `sessionId → { window, view }` for this Desktop process.
 *
 * The maps are plain fields, not React state: a view outliving a render cycle
 * is the entire point (I3). Nothing here schedules its own timers — the
 * heartbeat loop lives in the IPC wiring so tests can drive it directly.
 */
export class QaBrowserHost {
  private readonly entries = new Map<string, QaBrowserHostEntry>();
  private readonly adapters: QaBrowserHostAdapters;

  constructor(adapters: QaBrowserHostAdapters = createProductionAdapters()) {
    this.adapters = adapters;
  }

  get(sessionId: string): QaBrowserHostEntry | undefined {
    return this.entries.get(sessionId);
  }

  list(): QaBrowserHostEntry[] {
    return [...this.entries.values()];
  }

  has(sessionId: string): boolean {
    return this.entries.has(sessionId);
  }

  /**
   * Creates the view for a session, or returns the existing one.
   *
   * Creation is guarded by the session id: a repeat call for a live session
   * must never build a second view, because two views on one partition is both
   * a state loss (I3) and a second Chromium renderer.
   */
  ensure(input: { sessionId: string; taskId: string; issueId: string; partition: string }): QaBrowserHostEntry {
    const existing = this.entries.get(input.sessionId);
    if (existing) return existing;

    const window = this.adapters.createWindow({
      title: `QA · ${input.issueId}`,
      width: 1000,
      height: 720,
      // Never shown on creation: P3 says only an explicit takeover may raise
      // the window, never an agent start or a background resume.
      show: false,
    });
    const view = this.adapters.createView({ partition: input.partition });
    const profileDir = this.adapters.partitionDirFor(input.sessionId, input.partition);
    window.contentView.addChildView(view);
    this.applySecurityPolicy(view);

    const entry: QaBrowserHostEntry = {
      sessionId: input.sessionId,
      taskId: input.taskId,
      issueId: input.issueId,
      partition: input.partition,
      profileDir,
      window,
      view,
      visible: false,
      seq: 0,
      closing: false,
      executed: 0,
      console: [],
    };
    this.entries.set(input.sessionId, entry);
    return entry;
  }

  /**
   * Downloads are refused rather than prompted: a QA page asking to save a file
   * has no legitimate use here, and the app's main-session save dialog
   * (`index.ts`) is the wrong policy for this partition.
   */
  private applySecurityPolicy(view: QaBrowserView): void {
    // target=_blank navigates in place, it never reaches the OS shell: a QA
    // session must not be able to open the user's browser.
    view.webContents.setWindowOpenHandler((details) => {
      if (/^https?:/i.test(details.url)) {
        void Promise.resolve(view.webContents.loadURL(details.url)).catch(() => undefined);
      }
      return { action: "deny" };
    });
    view.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => {
      callback(false);
    });
    view.webContents.session.on("will-download", (event) => {
      (event as { preventDefault?: () => void } | undefined)?.preventDefault?.();
    });
  }

  /** Shows or hides the window without touching the view (I3). */
  setVisible(sessionId: string, visible: boolean): boolean {
    const entry = this.entries.get(sessionId);
    if (!entry) return false;
    entry.visible = visible;
    entry.view.setVisible(visible);
    if (visible) {
      entry.window.show();
    }
    return true;
  }

  /** Applies a geometry update in CSS px; main converts and clamps. */
  setBounds(sessionId: string, rect: QaBrowserRect, zoom: number): boolean {
    const entry = this.entries.get(sessionId);
    if (!entry) return false;
    // A minimised window reports meaningless content bounds; skip the update
    // and let the restore path recompute the full rectangle once.
    if (entry.window.isMinimized()) return true;
    entry.view.setBounds(computeViewBounds(rect, zoom, entry.window.getContentBounds()));
    return true;
  }

  /**
   * Brings the window forward. Called only from an explicit takeover (P3): an
   * agent action that raised this window would interrupt whatever the person
   * at the machine is doing.
   */
  raise(sessionId: string): boolean {
    const entry = this.entries.get(sessionId);
    if (!entry) return false;
    entry.visible = true;
    entry.view.setVisible(true);
    entry.window.show();
    entry.window.focus();
    return true;
  }

  async loadURL(sessionId: string, url: string): Promise<void> {
    const entry = this.entries.get(sessionId);
    if (!entry) return;
    await entry.view.webContents.loadURL(url);
  }

  currentURL(sessionId: string): string {
    const entry = this.entries.get(sessionId);
    if (!entry) return "";
    try {
      return entry.view.webContents.getURL();
    } catch {
      return "";
    }
  }

  /**
   * Captures the viewport. `capturePage()` is the only route: on a hidden
   * window `Page.captureScreenshot(fromSurface: true)` never returns (I5), and
   * `fromSurface: false` yields a two-colour image nobody can review.
   *
   * The artifact cap is decided here, before any request is sent: 4 MiB is
   * Desktop's limit, and the daemon's 6 MiB transport limit would happily
   * accept 4 MiB + 1 KiB.
   */
  async capture(sessionId: string, maxBytes: number): Promise<{ png: Buffer } | { tooLarge: number } | null> {
    const entry = this.entries.get(sessionId);
    if (!entry || entry.view.webContents.isDestroyed()) return null;
    let image: { toPNG(): Buffer; isEmpty(): boolean };
    try {
      image = await entry.view.webContents.capturePage();
    } catch {
      return null;
    }
    if (image.isEmpty()) return null;
    const png = image.toPNG();
    if (png.byteLength > maxBytes) return { tooLarge: png.byteLength };
    return { png };
  }

  /**
   * Phase one of cleanup (SDD §4.2): destroy the view, then remove the
   * partition directory. The order is not negotiable — removing the directory
   * first fights the live renderer's file locks and fails on Windows.
   *
   * Returns the directory it deleted so the caller can ack the daemon. A
   * failure to delete is reported rather than swallowed: the caller keeps the
   * entry queued for retry.
   */
  destroy(sessionId: string): { deleted: boolean; profileDir: string } | null {
    const entry = this.entries.get(sessionId);
    if (!entry) return null;
    this.entries.delete(sessionId);
    try {
      if (!entry.window.isDestroyed()) {
        entry.window.contentView.removeChildView(entry.view);
        entry.window.destroy();
      }
    } catch {
      // Already gone: the directory delete below is what still matters.
    }
    try {
      this.adapters.removePartitionDir(entry.profileDir);
      return { deleted: true, profileDir: entry.profileDir };
    } catch {
      // The caller keeps the daemon-side retry queue entry; reporting success
      // here would leave the directory behind with nothing scheduled to retry.
      return { deleted: false, profileDir: entry.profileDir };
    }
  }

  /** Tears down every view — used when the daemon goes away. */
  destroyAll(): string[] {
    const sessionIds = [...this.entries.keys()];
    for (const sessionId of sessionIds) this.destroy(sessionId);
    return sessionIds;
  }

  /**
   * Executes one daemon-issued command against the view.
   *
   * DOM actions go through the view's own in-process debugger, the same route
   * the R1 PoC measured: `Input.dispatchMouseEvent` three-step click,
   * `focus` + `Input.insertText` for fill, and a polled `Runtime.evaluate`
   * predicate for wait. Screenshots are NOT taken here — they must be
   * `capturePage()` on the hidden window (I5), so the caller uses `capture()`.
   */
  async execute(sessionId: string, command: QaBrowserHostCommand): Promise<QaBrowserHostCommandResult> {
    const entry = this.entries.get(sessionId);
    if (!entry || entry.view.webContents.isDestroyed()) {
      return { id: command.id, ok: false, error: "BROWSER_CAPABILITY_UNSUPPORTED" };
    }
    entry.executed += 1;
    const params = command.params ?? {};
    const wc = entry.view.webContents;
    try {
      switch (command.action) {
        case "browser_goto": {
          const url = typeof params.url === "string" ? params.url : "";
          if (!/^https?:/i.test(url)) {
            return { id: command.id, ok: false, error: "url must be absolute http or https" };
          }
          await wc.loadURL(url);
          const reported = readURLParts(this.currentURL(sessionId));
          return { id: command.id, ok: true, text: JSON.stringify(reported), result: reported };
        }
        case "browser_url": {
          const reported = readURLParts(this.currentURL(sessionId));
          return { id: command.id, ok: true, text: JSON.stringify(reported), result: reported };
        }
        case "browser_snapshot":
          return { id: command.id, ok: true, ...(await this.snapshot(sessionId)) };
        case "browser_click":
          return await this.click(sessionId, command.id, params);
        case "browser_fill":
          return await this.fill(sessionId, command.id, params);
        case "browser_wheel": {
          const deltaY = typeof params.deltaY === "number" ? params.deltaY : 0;
          const point = await this.viewportPoint(sessionId);
          await this.cdp(sessionId, "Input.dispatchMouseEvent", {
            type: "mouseWheel",
            x: typeof params.x === "number" ? params.x : point.x,
            y: typeof params.y === "number" ? params.y : point.y,
            deltaX: 0,
            deltaY,
            button: "none",
          });
          return { id: command.id, ok: true, text: `scrolled ${deltaY}px` };
        }
        case "browser_wait": {
          const condition = typeof params.condition === "string" ? params.condition : "";
          const timeoutMs = typeof params.timeout_ms === "number" ? params.timeout_ms : 5_000;
          return await this.wait(sessionId, command.id, condition, timeoutMs);
        }
        case "browser_console":
          return { id: command.id, ok: true, text: entry.console.join("\n") || "(no console output)" };
        default:
          return { id: command.id, ok: false, error: `unknown action ${command.action}` };
      }
    } catch (error) {
      return {
        id: command.id,
        ok: false,
        error: error instanceof Error ? error.message : "browser command failed",
      };
    }
  }

  /** Attaches the in-process debugger once per view. */
  private ensureDebugger(entry: QaBrowserHostEntry): void {
    const wc = entry.view.webContents;
    if (wc.debugger.isAttached()) return;
    wc.debugger.attach("1.3");
    entry.console = [];
    const listener = (_event: unknown, method: string, params: unknown) => {
      if (method !== "Runtime.consoleAPICalled") return;
      const args = (params as { args?: { value?: unknown; description?: unknown; type?: unknown }[] }).args ?? [];
      const line = args.map((a) => String(a.value ?? a.description ?? a.type ?? "")).join(" ");
      entry.console.push(line);
      // Bounded: a page in a console.log loop must not grow this without limit.
      if (entry.console.length > 200) entry.console.splice(0, entry.console.length - 200);
    };
    entry.consoleListener = listener;
    wc.debugger.on("message", listener);
    void wc.debugger.sendCommand("Runtime.enable").catch(() => undefined);
  }

  private async cdp(
    sessionId: string,
    method: string,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const entry = this.entries.get(sessionId);
    if (!entry) throw new Error("unknown session");
    this.ensureDebugger(entry);
    return await entry.view.webContents.debugger.sendCommand(method, params);
  }

  private async evaluate(sessionId: string, expression: string): Promise<unknown> {
    const result = await this.cdp(sessionId, "Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    const remote = result.result as { value?: unknown } | undefined;
    return remote?.value;
  }

  private async viewportPoint(sessionId: string): Promise<{ x: number; y: number }> {
    const size = (await this.evaluate(
      sessionId,
      "({ x: Math.round(innerWidth / 2), y: Math.round(innerHeight / 2) })",
    )) as { x: number; y: number } | undefined;
    return size ?? { x: 400, y: 300 };
  }

  /**
   * A flat snapshot of interactive nodes with stable refs.
   *
   * The raw a11y tree on an Electron target carries a large amount of
   * `none`/`InlineTextBox` noise, so only named, actionable roles are exposed
   * and each is held by index as `e<N>`, which the click/fill paths resolve in
   * the same page. Navigation invalidates them, which is exactly what makes
   * `BROWSER_STALE_REF` meaningful.
   */
  private async snapshot(
    sessionId: string,
  ): Promise<{ text?: string; result?: Record<string, unknown>; error?: string }> {
    await this.cdp(sessionId, "Accessibility.enable", {});
    const response = await this.cdp(sessionId, "Accessibility.getFullAXTree", {});
    const nodes = (response.nodes as { role?: { value?: string }; name?: { value?: string } }[]) ?? [];
    const actionable: { ref: string; role: string; name: string }[] = [];
    const roles = { button: true, textbox: true, link: true, checkbox: true, combobox: true, radio: true } as Record<string, true>;
    for (const node of nodes) {
      const role = node.role?.value ?? "";
      const name = node.name?.value ?? "";
      if (!roles[role] || !name) continue;
      actionable.push({ ref: `e${actionable.length + 1}`, role, name });
    }
    const listing = actionable.map((n) => `${n.ref} ${n.role} "${n.name}"`).join("\n");
    const payload = {
      nodes: actionable,
      url: readURLParts(this.currentURL(sessionId)),
    };
    return {
      text: listing || "(no interactive elements found)",
      result: payload,
    };
  }

  /** Resolves a `ref` or visible text to a viewport point, then clicks it. */
  private async click(
    sessionId: string,
    id: string,
    params: Record<string, unknown>,
  ): Promise<QaBrowserHostCommandResult> {
    const ref = typeof params.ref === "string" ? params.ref : "";
    const text = typeof params.text === "string" ? params.text : "";
    const point = (await this.evaluate(
      sessionId,
      buildClickExpression(ref, text),
    )) as { ok?: boolean; x?: number; y?: number; error?: string } | undefined;
    if (!point?.ok || typeof point.x !== "number" || typeof point.y !== "number") {
      return { id, ok: false, error: point?.error ?? "BROWSER_STALE_REF" };
    }
    for (const type of ["mouseMoved", "mousePressed", "mouseReleased"] as const) {
      await this.cdp(sessionId, "Input.dispatchMouseEvent", {
        type,
        x: point.x,
        y: point.y,
        button: type === "mouseMoved" ? "none" : "left",
        clickCount: type === "mouseMoved" ? 0 : 1,
      });
    }
    return { id, ok: true, text: `clicked at ${Math.round(point.x)},${Math.round(point.y)}` };
  }

  /** Focuses the target and inserts text at the caret. */
  private async fill(
    sessionId: string,
    id: string,
    params: Record<string, unknown>,
  ): Promise<QaBrowserHostCommandResult> {
    const ref = typeof params.ref === "string" ? params.ref : "";
    const value = typeof params.value === "string" ? params.value : "";
    const focused = (await this.evaluate(sessionId, buildFillExpression(ref))) as
      | { ok?: boolean; error?: string }
      | undefined;
    if (!focused?.ok) return { id, ok: false, error: focused?.error ?? "BROWSER_STALE_REF" };
    await this.cdp(sessionId, "Input.insertText", { text: value });
    return { id, ok: true, text: `filled ${value.length} characters` };
  }

  /** Polls a JS predicate. Bounded by the caller's 30 s tool budget. */
  private async wait(
    sessionId: string,
    id: string,
    condition: string,
    timeoutMs: number,
  ): Promise<QaBrowserHostCommandResult> {
    const deadline = Date.now() + Math.max(0, Math.min(timeoutMs, 30_000));
    let last: unknown;
    while (Date.now() < deadline) {
      last = await this.evaluate(sessionId, `Boolean(${condition})`);
      if (last === true) return { id, ok: true, text: "condition met" };
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    return { id, ok: false, error: `condition not met before the timeout (last=${String(last)})` };
  }
}

/**
 * Resolves a `ref`/text target inside the page.
 *
 * Refs are indices into the snapshot's actionable list, recomputed the same way
 * on the page side; a ref beyond the current list is a stale ref rather than a
 * silent miss.
 */
export function buildClickExpression(ref: string, text: string): string {
  const selector = `(function () {
    const roles = { button: 1, link: 1, checkbox: 1, combobox: 1, radio: 1 };
    const candidates = [...document.querySelectorAll('button,a,input,[role]')]
      .filter((el) => {
        const role = el.getAttribute('role') || (el.tagName === 'A' ? 'link' : el.tagName.toLowerCase());
        return roles[role] === 1 || el.tagName === 'INPUT';
      });
    const index = ${JSON.stringify(ref)}.startsWith('e') ? Number(${JSON.stringify(ref)}.slice(1)) - 1 : -1;
    const wanted = ${JSON.stringify(text)};
    let target = null;
    if (index >= 0) target = candidates[index] || null;
    else if (wanted) target = candidates.find((el) => (el.innerText || el.value || '').trim() === wanted)
      || candidates.find((el) => (el.innerText || '').includes(wanted)) || null;
    if (!target) return { ok: false, error: 'BROWSER_STALE_REF' };
    const r = target.getBoundingClientRect();
    return { ok: true, x: r.x + r.width / 2, y: r.y + r.height / 2 };
  })()`;
  return selector;
}

export function buildFillExpression(ref: string): string {
  return `(function () {
    const inputs = [...document.querySelectorAll('input,textarea,[contenteditable="true"]')];
    const index = ${JSON.stringify(ref)}.startsWith('e') ? Number(${JSON.stringify(ref)}.slice(1)) - 1 : 0;
    const target = inputs[index] || null;
    if (!target) return { ok: false, error: 'BROWSER_STALE_REF' };
    target.focus();
    return { ok: document.activeElement === target };
  })()`;
}

/**
 * Splits a URL into the host+path pair the daemon stores. The query is dropped
 * because it may carry a one-time token (SDD §3.3); this is the only place a
 * page URL becomes reportable state.
 */
export function readURLParts(raw: string): { host: string; path: string } {
  try {
    const parsed = new URL(raw);
    return { host: parsed.host, path: parsed.pathname };
  } catch {
    return { host: "", path: "" };
  }
}

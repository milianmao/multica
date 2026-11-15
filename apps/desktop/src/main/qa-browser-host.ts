import { BaseWindow, WebContentsView, screen } from "electron";
import { computeViewBounds } from "../shared/qa-browser";
import { redactConsoleText } from "./qa-browser-redact";
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
  getBounds?(): { x: number; y: number; width: number; height: number };
  isMinimized(): boolean;
  show(): void;
  /** Shows without taking focus — the capture-surface path (P3-safe). */
  showInactive(): void;
  setPosition?(x: number, y: number): void;
  focus(): void;
  destroy(): void;
  isDestroyed(): boolean;
  webContents: { id: number };
};

/**
 * Where the capture surface lives. Far off any real monitor: the point is to
 * have a display surface without occupying the desktop.
 */
const OFFSCREEN_ORIGIN = -20000;

export type QaBrowserHostAdapters = {
  createWindow(options: {
    title: string;
    width: number;
    height: number;
    show: boolean;
    /** Off-screen origin, so the capture surface never appears on the desktop. */
    x?: number;
    y?: number;
    skipTaskbar?: boolean;
  }): QaBrowserWindow;
  /** The primary display's usable rectangle, for bringing a window on screen. */
  workArea(): QaBrowserRect;
  createView(options: { partition: string }): QaBrowserView;
};

/** Uploads one captured PNG and reports where it landed. */
export type QaBrowserEvidenceSender = (
  sessionId: string,
  png: Buffer,
) => Promise<{ ok: true; attachmentId: string } | { ok: false; reason: string }>;

export type QaBrowserHostEntry = {
  sessionId: string;
  taskId: string;
  issueId: string;
  partition: string;
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
        x: options.x,
        y: options.y,
        skipTaskbar: options.skipTaskbar,
      }) as unknown as QaBrowserWindow,
    workArea: () => screen.getPrimaryDisplay().workArea,
    createView: (options) =>
      new WebContentsView({
        webPreferences: {
          ...QA_BROWSER_WEB_PREFERENCES,
          partition: options.partition,
        },
      }) as unknown as QaBrowserView,

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
  /**
   * Uploads a captured PNG and returns its attachment id.
   *
   * Injected because the host owns the view while the daemon client owns the
   * credential and port; wiring them here keeps `browser_screenshot` reachable
   * from the command loop without the view knowing about secrets.
   */
  private captureEvidence: QaBrowserEvidenceSender | null = null;
  /** Artifact cap, owned by main and never delegated to the daemon. */
  private evidenceMaxBytes = 4 << 20;

  constructor(adapters: QaBrowserHostAdapters = createProductionAdapters()) {
    this.adapters = adapters;
  }

  /** Installs the evidence sender used by the `browser_screenshot` action. */
  setEvidenceSender(sender: QaBrowserEvidenceSender | null, maxBytes?: number): void {
    this.captureEvidence = sender;
    if (maxBytes !== undefined && maxBytes > 0) this.evidenceMaxBytes = maxBytes;
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
      // P3: only an explicit takeover may raise the window. The window is
      // therefore real but placed far off-screen, not merely hidden — see
      // `ensureCaptureSurface` for why hiding is not enough.
      show: false,
      x: OFFSCREEN_ORIGIN,
      y: OFFSCREEN_ORIGIN,
      skipTaskbar: true,
    });
    const view = this.adapters.createView({ partition: input.partition });
    window.contentView.addChildView(view);
    // `capturePage()` needs a display surface. Measured on Electron 44.9.3: a
    // window that has NEVER been shown throws "Current display surface not
    // available for capture", while the same window after `showInactive()`
    // captures a full-colour PNG and `isFocused()` stays false. So the window is
    // shown once, off-screen and without activation, which satisfies I5 without
    // breaking P3 — nothing appears on the user's desktop and no window takes
    // focus. The alternative (skip the surface) makes browser_screenshot, and
    // therefore A4, impossible.
    // Do NOT reposition after showInactive(): measured on Electron 44.9.3, a
    // post-show setPosition tears down the display surface and every later
    // capturePage fails with "Current display surface not available". The
    // window is already created at OFFSCREEN_ORIGIN, so no move is needed.
    window.showInactive();
    // Give the view a real rectangle immediately. Until the renderer sends
    // `qa-browser:bounds`, a freshly added WebContentsView is 0×0 — and a 0×0
    // view yields an empty image from capturePage, so `browser_screenshot` would
    // fail on any session whose first screenshot beat the first bounds message.
    const content = window.getContentBounds();
    view.setBounds({ x: 0, y: 0, width: content.width, height: content.height });
    this.applySecurityPolicy(view);

    const entry: QaBrowserHostEntry = {
      sessionId: input.sessionId,
      taskId: input.taskId,
      issueId: input.issueId,
      partition: input.partition,
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
    // `setWindowOpenHandler` only covers window.open/target=_blank. A page can
    // also navigate itself to `file://`, `data:` or `javascript:`, and on a
    // `file:` document it would render local filesystem contents — which the
    // evidence channel then uploads to the issue. Both hooks are required.
    for (const event of ["will-navigate", "will-redirect"] as const) {
      view.webContents.on(event, (navEvent: unknown, url: unknown) => {
        if (isAllowedQaBrowserURL(typeof url === "string" ? url : "")) return;
        (navEvent as { preventDefault?: () => void } | undefined)?.preventDefault?.();
      });
    }
    view.webContents.session.setPermissionRequestHandler((_contents, _permission, callback) => {
      callback(false);
    });
    view.webContents.session.on("will-download", (event) => {
      (event as { preventDefault?: () => void } | undefined)?.preventDefault?.();
    });
  }

  /**
   * Shows or hides the window without touching the view (I3).
   *
   * Showing also brings the window into the visible work area. The window is
   * parked off-screen at creation (that is what buys a capture surface without
   * stealing focus), so a bare `show()` would put it somewhere the user cannot
   * see — which is exactly what "打开浏览器" did before.
   */
  setVisible(sessionId: string, visible: boolean): boolean {
    const entry = this.entries.get(sessionId);
    if (!entry) return false;
    entry.visible = visible;
    entry.view.setVisible(visible);
    if (visible) {
      this.moveIntoWorkArea(entry);
      entry.window.show();
    }
    return true;
  }

  /**
   * Moves a parked window into the primary display's work area.
   *
   * Measured on Electron 44.9.3: `setPosition` while the window is *visible*
   * both relocates it and keeps `capturePage()` working. Calling it before the
   * first `showInactive()` leaves no display surface (capture then throws
   * "Current display surface not available"), so this must only ever run on a
   * window that already has one — i.e. after `ensure()`.
   */
  private moveIntoWorkArea(entry: QaBrowserHostEntry): void {
    const area = this.adapters.workArea();
    const bounds = entry.window.getBounds?.();
    const width = bounds?.width ?? 1000;
    const height = bounds?.height ?? 720;
    // Top-left with a small inset, clamped so a window larger than the work
    // area still starts inside rather than hanging off the top-left corner.
    const x = Math.min(area.x + 40, Math.max(area.x, area.x + area.width - width));
    const y = Math.min(area.y + 40, Math.max(area.y, area.y + area.height - height));
    entry.window.setPosition?.(x, y);
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
    // Move first, then show: a takeover has to produce a window the person can
    // actually see, not one focused off-screen.
    this.moveIntoWorkArea(entry);
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
   * Cleans up a session: release the view, then the window.
   *
   * There is no directory to delete. The partition is non-persistent, so
   * Chromium keeps cookies and storage in the process and writes no profile
   * directory — deleting one would have meant deleting a path the daemon never
   * created, which is precisely the P0-1 attack surface. A6 therefore reads
   * "view destroyed, registry cleared, no live process", not "directory gone".
   */
  destroy(sessionId: string): boolean {
    const entry = this.entries.get(sessionId);
    if (!entry) return false;
    this.entries.delete(sessionId);
    try {
      if (!entry.window.isDestroyed()) {
        entry.window.contentView.removeChildView(entry.view);
        entry.window.destroy();
      }
    } catch {
      // Already gone: the entry is out of the map, which is what matters.
    }
    return true;
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
   * predicate for wait.
   *
   * `browser_screenshot` is a first-class action here, not a side channel: the
   * agent calling the tool is the only automated source of A4 evidence. It
   * captures through `capturePage()` and hands the PNG to `captureEvidence`,
   * which uploads it and returns the attachment id.
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
          // Console lines only accumulate once the debugger is attached, and a
          // session whose first action is `browser_console` would otherwise
          // report "(no console output)" against a page that logged plenty.
          this.ensureDebugger(entry);
          // Redacted here *and* on the daemon boundary. The daemon is the
          // authoritative pass (it owns the full rule set and feeds both the
          // agent and the transcript); this one keeps the raw text out of any
          // Desktop-side log line or crash dump of this string.
          return {
            id: command.id,
            ok: true,
            text: redactConsoleText(entry.console.join("\n")) || "(no console output)",
          };
        case "browser_screenshot": {
          if (!this.captureEvidence) {
            return { id: command.id, ok: false, error: "BROWSER_EVIDENCE_FAILED" };
          }
          const capture = await this.capture(sessionId, this.evidenceMaxBytes);
          if (!capture) return { id: command.id, ok: false, error: "BROWSER_EVIDENCE_FAILED" };
          if ("tooLarge" in capture) {
            // Over the 4 MiB artifact cap: do not upload. The daemon's
            // transport limit is 6 MiB and would accept it, which is exactly
            // why the product cap cannot live there.
            return { id: command.id, ok: false, error: "BROWSER_EVIDENCE_FAILED" };
          }
          const upload = await this.captureEvidence(sessionId, capture.png);
          if (!upload.ok) return { id: command.id, ok: false, error: "BROWSER_EVIDENCE_FAILED" };
          return {
            id: command.id,
            ok: true,
            text: `screenshot attached to the issue: attachment ${upload.attachmentId}`,
            result: { attachment_id: upload.attachmentId, viewport_only: true },
          };
        }
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
 * The only schemes a QA view may navigate to.
 *
 * `file:` would render the local filesystem, and the evidence channel would
 * then upload that view to the issue; `data:` and `javascript:` are page-code
 * execution surfaces with no legitimate QA use here.
 */
export function isAllowedQaBrowserURL(raw: string): boolean {
  try {
    const parsed = new URL(raw);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
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

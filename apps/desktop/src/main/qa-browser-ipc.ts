import { ipcMain, BrowserWindow } from "electron";
import {
  QA_BROWSER_BOUNDS_CHANNEL,
  QA_BROWSER_EVIDENCE_CHANNEL,
  QA_BROWSER_OPEN_CHANNEL,
  QA_BROWSER_RELEASE_CHANNEL,
  QA_BROWSER_SESSION_STATE_CHANNEL,
  QA_BROWSER_TAKEOVER_CHANNEL,
  QA_BROWSER_TOGGLE_CHANNEL,
  parseQaBrowserBoundsRequest,
  parseQaBrowserOpenRequest,
  parseQaBrowserSessionRequest,
  parseQaBrowserToggleRequest,
} from "../shared/qa-browser";
import type { QaBrowserActionResult, QaBrowserEvidenceResult, QaBrowserFailureReason } from "../shared/qa-browser";
import { QaBrowserHost } from "./qa-browser-host";
import type { QaBrowserDaemonPort, QaBrowserHostCommandResult } from "./qa-browser-client";
import {
  ackQaBrowserSessionClosed,
  openQaBrowserSession,
  readQaBrowserHostSecret,
  readQaBrowserSessionState,
  registerQaBrowserHost,
  releaseQaBrowserSession,
  takeoverQaBrowserSession,
} from "./qa-browser-client";

/**
 * QA built-in isolated browser: the renderer↔main IPC surface (SDD §4.4).
 *
 * Two rules hold this together:
 *
 *   1. The sender must be a real window (`BrowserWindow.fromWebContents`), the
 *      same gate `window:open-issue` uses — an IPC channel reachable from a
 *      view that is not a Multica window is an uncontrolled control surface.
 *   2. `sessionId` is always checked against the set this window actually owns.
 *      The renderer sending an arbitrary id must never reach another task's
 *      view, because a compromised renderer would otherwise control every
 *      browser this Desktop is hosting.
 */

/** Delivers a renderer's screenshot for one session. 4 MiB is ours to enforce. */
export const QA_BROWSER_EVIDENCE_MAX_BYTES = 4 << 20;

export type QaBrowserDeps = {
  /** Resolves the daemon the current profile is running, or null when stopped. */
  resolveDaemon(): Promise<QaBrowserDaemonPort | null>;
  host?: QaBrowserHost;
  /** Uploads the captured PNG; injected so tests never touch the network. */
  uploadEvidence?: (
    target: QaBrowserDaemonPort,
    sessionId: string,
    png: Buffer,
    meta: { urlHost: string; urlPath: string },
  ) => Promise<{ ok: true; attachmentId: string } | { ok: false; reason: string }>;
};

/** Sessions each window opened, keyed by that window's webContents id. */
const ownedSessions = new Map<number, Set<string>>();

function sessionsForWindow(windowId: number): Set<string> {
  let owned = ownedSessions.get(windowId);
  if (!owned) {
    owned = new Set<string>();
    ownedSessions.set(windowId, owned);
  }
  return owned;
}

function releaseWindowSessions(windowId: number): void {
  ownedSessions.delete(windowId);
}

/**
 * Registers every channel and returns the teardown that removes them.
 *
 * A returned teardown rather than module-level `ipcMain.handle` calls keeps the
 * registration testable: a test registers against a fake `ipcMain`, drives each
 * channel, and asserts the sender/ownership checks without booting Electron.
 */
export function registerQaBrowserIpc(deps: QaBrowserDeps): () => void {
  const host = deps.host ?? new QaBrowserHost();

  /** Gate shared by every channel: the sender must be one of our windows. */
  const senderWindow = (event: { sender: Electron.WebContents }): BrowserWindow | null =>
    BrowserWindow.fromWebContents(event.sender);

  const authorized = (
    event: { sender: Electron.WebContents },
    sessionId: string,
  ): { windowId: number } | null => {
    const window = senderWindow(event);
    if (!window) return null;
    const owned = ownedSessions.get(window.id);
    if (!owned || !owned.has(sessionId)) return null;
    return { windowId: window.id };
  };

  const failure = (reason: QaBrowserFailureReason): QaBrowserActionResult => ({ ok: false, reason });

  ipcMain.handle(QA_BROWSER_OPEN_CHANNEL, async (event, request: unknown) => {
    const window = senderWindow(event);
    const parsed = parseQaBrowserOpenRequest(request);
    if (!window || !parsed) return failure("invalid_request");
    const target = await deps.resolveDaemon();
    if (!target) return failure("daemon_unavailable");
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return failure("daemon_unavailable");

    const opened = await openQaBrowserSession(target, secret, {
      taskId: parsed.taskId,
      issueId: parsed.issueId,
    });
    if (!opened.ok) {
      return failure(opened.reason === "session_ended" ? "session_ended" : "daemon_unavailable");
    }

    host.ensure({
      sessionId: opened.session.session_id,
      taskId: parsed.taskId,
      issueId: parsed.issueId,
      partition: opened.session.partition,
    });
    sessionsForWindow(window.id).add(opened.session.session_id);

    const registered = await registerQaBrowserHost(target, secret, {
      sessionId: opened.session.session_id,
      taskId: parsed.taskId,
      windowId: String(window.id),
      // No directory is reported: the partition is non-persistent and the
      // daemon owns no path, so there is nothing for it to delete.
      cdpReady: true,
    });
    if (!registered.ok) {
      host.destroy(opened.session.session_id);
      sessionsForWindow(window.id).delete(opened.session.session_id);
      return failure("daemon_unavailable");
    }
    return { ok: true, session: registered.session };
  });

  ipcMain.handle(QA_BROWSER_SESSION_STATE_CHANNEL, async (event, request: unknown) => {
    const window = senderWindow(event);
    if (!window) return failure("invalid_request");
    const owned = ownedSessions.get(window.id);
    if (!owned || owned.size === 0) return failure("not_this_window");
    // With one owned session the request may omit the id; with several it must
    // name one, because picking arbitrarily would show another task's state.
    const requested = parseQaBrowserSessionRequest(request);
    const sessionId = requested?.sessionId ?? (owned.size === 1 ? [...owned][0] : null);
    if (!sessionId || !owned.has(sessionId)) return failure("not_this_window");
    const target = await deps.resolveDaemon();
    if (!target) return failure("daemon_unavailable");
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return failure("daemon_unavailable");
    // State comes from the daemon, never from local guesses: the lock owner and
    // the state machine have exactly one authority (SDD §7). The read carries
    // no heartbeat: liveness is the loop's job, and a second writer would
    // advance the same sequence number twice for one observation.
    const result = await readQaBrowserSessionState(target, secret, sessionId);
    if (!result.ok) return failure(result.reason === "session_ended" ? "session_ended" : "daemon_unavailable");
    return { ok: true, session: result.session };
  });

  ipcMain.on(QA_BROWSER_TOGGLE_CHANNEL, (event, request: unknown) => {
    const parsed = parseQaBrowserToggleRequest(request);
    if (!parsed) return;
    if (!authorized(event, parsed.sessionId)) return;
    // Visibility never rebuilds the view (I3): a tab switch only toggles.
    host.setVisible(parsed.sessionId, parsed.visible);
  });

  ipcMain.on(QA_BROWSER_BOUNDS_CHANNEL, (event, request: unknown) => {
    const parsed = parseQaBrowserBoundsRequest(request);
    if (!parsed) return;
    if (!authorized(event, parsed.sessionId)) return;
    const entry = host.get(parsed.sessionId);
    const zoom = entry ? readZoom(entry.sessionId, host) : 1;
    host.setBounds(parsed.sessionId, parsed.rect, zoom);
  });

  ipcMain.handle(QA_BROWSER_TAKEOVER_CHANNEL, async (event, request: unknown) => {
    const parsed = parseQaBrowserSessionRequest(request);
    if (!parsed) return failure("invalid_request");
    if (!authorized(event, parsed.sessionId)) return failure("not_this_window");
    const target = await deps.resolveDaemon();
    if (!target) return failure("daemon_unavailable");
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return failure("daemon_unavailable");
    const result = await takeoverQaBrowserSession(target, secret, parsed.sessionId);
    if (!result.ok) return failure(result.reason === "session_ended" ? "session_ended" : "daemon_unavailable");
    // Only an explicit takeover raises the window (P3).
    host.raise(parsed.sessionId);
    return { ok: true, session: result.session };
  });

  ipcMain.handle(QA_BROWSER_RELEASE_CHANNEL, async (event, request: unknown) => {
    const parsed = parseQaBrowserSessionRequest(request);
    if (!parsed) return failure("invalid_request");
    if (!authorized(event, parsed.sessionId)) return failure("not_this_window");
    const target = await deps.resolveDaemon();
    if (!target) return failure("daemon_unavailable");
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return failure("daemon_unavailable");
    const result = await releaseQaBrowserSession(target, secret, parsed.sessionId);
    if (!result.ok) return failure(result.reason === "session_ended" ? "session_ended" : "daemon_unavailable");
    return { ok: true, session: result.session };
  });

  ipcMain.handle(QA_BROWSER_EVIDENCE_CHANNEL, async (event, request: unknown) => {
    const parsed = parseQaBrowserSessionRequest(request);
    if (!parsed) return failure("invalid_request");
    if (!authorized(event, parsed.sessionId)) return failure("not_this_window");
    const target = await deps.resolveDaemon();
    if (!target) return failure("daemon_unavailable");
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return failure("daemon_unavailable");
    if (!deps.uploadEvidence) return evidenceFailure();

    // Two attempts, as the contract allows; a third would only delay the
    // session's terminal state.
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const capture = await host.capture(parsed.sessionId, QA_BROWSER_EVIDENCE_MAX_BYTES);
      if (!capture) return evidenceFailure();
      if ("tooLarge" in capture) {
        // Over the artifact cap: fail here and do NOT send. The daemon's
        // transport limit is 6 MiB and would accept this, which is exactly why
        // the check cannot live there.
        return evidenceFailure();
      }
      const url = readReportedURL(host.currentURL(parsed.sessionId));
      const upload = await deps.uploadEvidence(target, parsed.sessionId, capture.png, {
        urlHost: url.host,
        urlPath: url.path,
      });
      if (upload.ok) {
        const result: QaBrowserEvidenceResult = { ok: true, attachmentId: upload.attachmentId };
        return result;
      }
    }
    return evidenceFailure();
  });

  return () => {
    ipcMain.removeHandler(QA_BROWSER_OPEN_CHANNEL);
    ipcMain.removeHandler(QA_BROWSER_SESSION_STATE_CHANNEL);
    ipcMain.removeAllListeners(QA_BROWSER_TOGGLE_CHANNEL);
    ipcMain.removeAllListeners(QA_BROWSER_BOUNDS_CHANNEL);
    ipcMain.removeHandler(QA_BROWSER_TAKEOVER_CHANNEL);
    ipcMain.removeHandler(QA_BROWSER_RELEASE_CHANNEL);
    ipcMain.removeHandler(QA_BROWSER_EVIDENCE_CHANNEL);
  };
}

const evidenceFailure = (): QaBrowserEvidenceResult => ({ ok: false, reason: "evidence_failed" });

function readZoom(sessionId: string, host: QaBrowserHost): number {
  const entry = host.get(sessionId);
  if (!entry) return 1;
  try {
    return entry.view.webContents.getZoomFactor();
  } catch {
    return 1;
  }
}

function readReportedURL(raw: string): { host: string; path: string } {
  try {
    const parsed = new URL(raw);
    return { host: parsed.host, path: parsed.pathname };
  } catch {
    return { host: "", path: "" };
  }
}

/**
 * Drops a window's ownership records and tears its views down.
 *
 * A closed window must not leave a live view behind: the daemon would keep
 * heartbeating a session nobody can see, and the WebContentsView would outlive
 * every surface that could close it.
 */
export function forgetQaBrowserWindow(
  windowId: number,
  host: QaBrowserHost,
  deps?: Pick<QaBrowserDeps, "resolveDaemon">,
): void {
  const owned = ownedSessions.get(windowId);
  if (owned) {
    for (const sessionId of owned) {
      host.destroy(sessionId);
      // Best-effort phase-two ack. A window close is the one path where nobody
      // is left to answer the daemon's directive, so without this the session
      // waits out its no-ack timeout; the daemon also has that timeout as a
      // backstop, so a failure here is a delay, not a leak.
      if (deps) void ackClosedInBackground(deps, sessionId);
    }
  }
  releaseWindowSessions(windowId);
}

async function ackClosedInBackground(
  deps: Pick<QaBrowserDeps, "resolveDaemon">,
  sessionId: string,
): Promise<void> {
  try {
    const target = await deps.resolveDaemon();
    if (!target) return;
    const secret = await readQaBrowserHostSecret(target.profile);
    if (!secret) return;
    await ackQaBrowserSessionClosed(target, secret, sessionId);
  } catch {
    // The daemon's no-ack timeout covers it.
  }
}

/** Ack helper for the closing directive: destroy, then tell the daemon. */
export async function closeQaBrowserSession(
  host: QaBrowserHost,
  target: QaBrowserDaemonPort,
  secret: string,
  sessionId: string,
): Promise<void> {
  host.destroy(sessionId);
  await ackQaBrowserSessionClosed(target, secret, sessionId);
}

/** Applies command results to the host and returns what the daemon should hear. */
export async function runQaBrowserCommands(
  host: QaBrowserHost,
  sessionId: string,
  commands: { id: string; action: string; params?: Record<string, unknown> }[],
): Promise<QaBrowserHostCommandResult[]> {
  const results: QaBrowserHostCommandResult[] = [];
  for (const command of commands) {
    results.push(await host.execute(sessionId, command));
  }
  return results;
}

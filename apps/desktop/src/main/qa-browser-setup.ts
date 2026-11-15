import { QaBrowserHost } from "./qa-browser-host";
import {
  closeQaBrowserSession,
  forgetQaBrowserWindow,
  registerQaBrowserIpc,
  runQaBrowserCommands,
} from "./qa-browser-ipc";
import { startQaBrowserHostLoop } from "./qa-browser-runtime";
import type { QaBrowserHostLoop } from "./qa-browser-runtime";
import { resolveRunningDaemonPort } from "./daemon-manager";
import { uploadQaBrowserEvidence } from "./qa-browser-evidence";

/**
 * QA built-in isolated browser: one call that installs every Desktop-side
 * piece and returns its teardown (SDD §12 stage 2–5).
 *
 * Registration is a single seam on purpose. The feature must add no process-
 * level debug port (I1) and no second Chromium, and everything it installs is
 * removed by the returned `dispose`.
 *
 * **What the feature flag does and does not gate.** `qa_browser` is read on the
 * server: it decides whether a task's agent is given the `multica-browser`
 * tools, and the renderer decides whether to draw the sidebar card. Desktop
 * main cannot read that decision — it has no server session — so it always
 * installs the plumbing. That is deliberate and bounded: with no session there
 * is no view, no poll and no command, so the idle cost is a registered IPC
 * surface plus a loop that returns immediately. The earlier comment here
 * claimed main was flag-gated; it never was, and saying so was wrong.
 */

/**
 * The process-wide host, exposed for the window-close paths.
 *
 * A closed window's views must go away immediately, and those handlers live in
 * the module that creates windows — threading the host through unrelated window
 * code would be worse. Null until `setupQaBrowser` runs, which is what makes an
 * early close a no-op.
 */
let activeHost: QaBrowserHost | null = null;

/**
 * Closes a window's browser sessions and acks them.
 *
 * Exposed as one call so the two window-close handlers (main window and
 * dedicated issue window) share it and neither forgets the ack.
 */
export function closeQaBrowserWindow(windowId: number): void {
  const host = activeHost;
  if (!host) return;
  forgetQaBrowserWindow(windowId, host, { resolveDaemon: resolveRunningDaemonPort });
}

export function setupQaBrowser(): { dispose: () => void } {
  const host = new QaBrowserHost();
  activeHost = host;

  /**
   * The sender `browser_screenshot` uses. It resolves the daemon on every call
   * rather than capturing one at setup: a restart changes the port, and a
   * screenshot must not fail because the daemon moved five minutes ago.
   */
  host.setEvidenceSender(async (sessionId, png) => {
    const target = await resolveRunningDaemonPort();
    if (!target) return { ok: false, reason: "daemon_unavailable" };
    const url = host.currentURL(sessionId);
    const upload = await uploadQaBrowserEvidence(target, sessionId, png, splitURL(url));
    return upload.ok ? { ok: true, attachmentId: upload.attachmentId } : { ok: false, reason: upload.reason };
  });

  const disposeIpc = registerQaBrowserIpc({
    resolveDaemon: resolveRunningDaemonPort,
    host,
    uploadEvidence: uploadQaBrowserEvidence,
  });

  let loop: QaBrowserHostLoop | null = null;
  // The loop is started lazily: with no session it must not poll the daemon
  // every five seconds for the whole life of the app.
  const ensureLoop = (): void => {
    if (loop) return;
    loop = startQaBrowserHostLoop({
      host,
      resolveDaemon: resolveRunningDaemonPort,
      closeSession: (target, secret, sessionId) => closeQaBrowserSession(host, target, secret, sessionId),
      executeCommands: (sessionId, commands) => runQaBrowserCommands(host, sessionId, commands),
      onDaemonLost: () => {
        // Nothing to do beyond the destroy the loop already performed: the
        // renderer learns through the next session-state read.
        loop?.stop();
        loop = null;
      },
    });
  };
  ensureLoop();

  return {
    dispose() {
      loop?.stop();
      loop = null;
      disposeIpc();
      host.destroyAll();
      activeHost = null;
    },
  };
}

function splitURL(raw: string): { urlHost: string; urlPath: string } {
  try {
    const parsed = new URL(raw);
    return { urlHost: parsed.host, urlPath: parsed.pathname };
  } catch {
    return { urlHost: "", urlPath: "" };
  }
}

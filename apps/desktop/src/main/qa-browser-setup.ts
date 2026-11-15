import { QaBrowserHost } from "./qa-browser-host";
import { closeQaBrowserSession, registerQaBrowserIpc, runQaBrowserCommands } from "./qa-browser-ipc";
import { startQaBrowserHostLoop } from "./qa-browser-runtime";
import type { QaBrowserHostLoop } from "./qa-browser-runtime";
import { resolveRunningDaemonPort } from "./daemon-manager";
import { uploadQaBrowserEvidence } from "./qa-browser-evidence";

/**
 * QA built-in isolated browser: one call that installs every Desktop-side
 * piece and returns its teardown (SDD §12 stage 2–5).
 *
 * Registration is a single seam on purpose. The feature must add no process-
 * level debug port (I1), no second Chromium, and nothing reachable while the
 * flag is off — so everything it installs is installed here and removed by the
 * returned `dispose`.
 */
/**
 * The process-wide host, exposed for the window-close path.
 *
 * A closed window's views must go away immediately, and that handler runs in
 * the module that creates windows — passing the host through every call site
 * would thread a desktop-only concern through unrelated window code. Null until
 * `setupQaBrowser` runs, which is what makes an early close a no-op.
 */
let activeHost: QaBrowserHost | null = null;

export function qaBrowserHostOrNull(): QaBrowserHost | null {
  return activeHost;
}

export function setupQaBrowser(): { dispose: () => void } {
  const host = new QaBrowserHost();
  activeHost = host;

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

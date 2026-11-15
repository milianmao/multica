import type { QaBrowserDaemonPort, QaBrowserHostCommandResult } from "./qa-browser-client";
import { heartbeatQaBrowserHost, readQaBrowserHostSecret } from "./qa-browser-client";
import type { QaBrowserHost } from "./qa-browser-host";

/**
 * QA built-in isolated browser: the Desktop-side host loop.
 *
 * Desktop is the only component that can see the view, so it is also the only
 * one that can report liveness, run queued commands and act on a cleanup
 * directive. All three ride one heartbeat because the contract has no separate
 * push channel (SDD §3.2, §4.2).
 *
 * The loop is deliberately not `setInterval`: a long poll already spaces
 * requests, and overlapping ticks would advance the sequence number twice for
 * one observation.
 */

/**
 * Wall-clock spacing between heartbeat starts.
 *
 * Worst case an iteration takes `HEARTBEAT_INTERVAL_MS + LONG_POLL_MS` (6s at
 * these values), still an order of magnitude inside the daemon's 30s orphan
 * threshold. Do not raise this without raising that threshold first.
 */
const HEARTBEAT_INTERVAL_MS = 2_000;

export type QaBrowserHostLoopDeps = {
  host: QaBrowserHost;
  resolveDaemon(): Promise<QaBrowserDaemonPort | null>;
  /** Destroys the view, then acks, when the daemon says the session is closing. */
  closeSession(target: QaBrowserDaemonPort, secret: string, sessionId: string): Promise<void>;
  executeCommands(sessionId: string, commands: { id: string; action: string; params?: Record<string, unknown> }[]): Promise<QaBrowserHostCommandResult[]>;
  onDaemonLost(): void;
  setInterval?: typeof setInterval;
  clearInterval?: typeof clearInterval;
  intervalMs?: number;
};

export type QaBrowserHostLoop = { stop(): void };

/**
 * Starts the loop for the sessions this Desktop hosts.
 *
 * A session whose heartbeat reports `orphaned` is left alone: only the daemon
 * can decide an orphan's fate, and Desktop reviving one locally would defeat
 * the "a missed heartbeat is not recoverable" rule (SDD §9 case 2).
 */
export function startQaBrowserHostLoop(deps: QaBrowserHostLoopDeps): QaBrowserHostLoop {
  const schedule = deps.setInterval ?? setInterval;
  const cancel = deps.clearInterval ?? clearInterval;
  const intervalMs = deps.intervalMs ?? HEARTBEAT_INTERVAL_MS;
  const sequences = new Map<string, number>();
  let inFlight = false;
  let stopped = false;

  const tick = async (): Promise<void> => {
    if (inFlight || stopped) return;
    inFlight = true;
    try {
      const sessions = deps.host.list();
      if (sessions.length === 0) return;
      const target = await deps.resolveDaemon();
      if (!target) {
        // Daemon gone: destroy every view rather than leave a window that
        // looks usable but answers nothing (SDD §4.2).
        deps.host.destroyAll();
        deps.onDaemonLost();
        return;
      }
      const secret = await readQaBrowserHostSecret(target.profile);
      if (!secret) {
        deps.host.destroyAll();
        deps.onDaemonLost();
        return;
      }
      for (const entry of sessions) {
        const seq = (sequences.get(entry.sessionId) ?? 0) + 1;
        sequences.set(entry.sessionId, seq);
        const result = await heartbeatQaBrowserHost(target, secret, {
          sessionId: entry.sessionId,
          seq,
          viewAlive: !entry.view.webContents.isDestroyed(),
          url: entry.view.webContents.isDestroyed()
            ? undefined
            : splitReportedURL(deps.host.currentURL(entry.sessionId)),
        });
        if (!result.ok) {
          if (result.reason === "session_ended") {
            deps.host.destroy(entry.sessionId);
            sequences.delete(entry.sessionId);
          }
          continue;
        }
        if (result.directive === "close_view") {
          await deps.closeSession(target, secret, entry.sessionId);
          sequences.delete(entry.sessionId);
          continue;
        }
        if (result.commands && result.commands.length > 0) {
          const results = await deps.executeCommands(entry.sessionId, result.commands);
          // Results travel on the next heartbeat: the poll's own body carried
          // the previous batch, and splitting them keeps one round trip.
          const followUp = await heartbeatQaBrowserHost(target, secret, {
            sessionId: entry.sessionId,
            seq: (sequences.get(entry.sessionId) ?? seq) + 1,
            viewAlive: !entry.view.webContents.isDestroyed(),
            results,
          });
          sequences.set(entry.sessionId, (sequences.get(entry.sessionId) ?? seq) + 1);
          if (followUp.ok && followUp.directive === "close_view") {
            await deps.closeSession(target, secret, entry.sessionId);
            sequences.delete(entry.sessionId);
          }
        }
      }
    } finally {
      inFlight = false;
    }
  };

  const handle = schedule(() => {
    void tick();
  }, intervalMs);
  return {
    stop() {
      stopped = true;
      cancel(handle);
    },
  };
}

function splitReportedURL(raw: string): { host: string; path: string } {
  try {
    const parsed = new URL(raw);
    return { host: parsed.host, path: parsed.pathname };
  } catch {
    return { host: "", path: "" };
  }
}

import { readFile } from "fs/promises";
import { join } from "path";
import { profileDir } from "./daemon-profile";
import type { QaBrowserSessionView } from "../shared/qa-browser-types";
import { parseQaBrowserSessionView } from "../shared/qa-browser-types";

/**
 * Desktop's side of the QA browser host plane (SDD §3.1).
 *
 * The credential is the daemon's per-profile `browser-host.secret`, read off
 * disk from the profile directory Desktop already owns. It never travels over
 * HTTP in a response and never enters a log line: it is only ever placed in an
 * outbound `Authorization` header.
 */

const HOST_SECRET_FILE = "browser-host.secret";
const REQUEST_TIMEOUT_MS = 15_000;
/** Shorter than the request timeout so a long poll cannot race it. */
/**
 * How long one heartbeat blocks waiting for commands.
 *
 * This is the floor on the gap between two heartbeats, so it must stay far
 * below the daemon's `defaultBrowserHeartbeatTimeout` (30s): at 10s the poll
 * itself consumed the liveness budget and an idle session orphaned itself.
 */
const LONG_POLL_MS = 4_000;

export type QaBrowserDaemonPort = {
  port: number;
  profile: string;
};

export type QaBrowserHostCommand = {
  id: string;
  action: string;
  params?: Record<string, unknown>;
};

export type QaBrowserHostCommandResult = {
  id: string;
  ok: boolean;
  error?: string;
  text?: string;
  result?: Record<string, unknown>;
};

export type QaBrowserHostResult =
  | {
      ok: true;
      session: QaBrowserSessionView;
      directive?: string;
      commands?: QaBrowserHostCommand[];
    }
  | {
      ok: false;
      reason: "daemon_unavailable" | "session_ended" | "invalid_request";
      message?: string;
    };

/**
 * Reads the host secret for a profile. A missing file means the daemon has not
 * started the feature (or is older than it), which callers report as
 * `daemon_unavailable` rather than retrying forever.
 */
export async function readQaBrowserHostSecret(profile: string): Promise<string | null> {
  try {
    const raw = await readFile(join(profileDir(profile), HOST_SECRET_FILE), "utf8");
    const secret = raw.trim();
    return secret.length >= 32 ? secret : null;
  } catch {
    return null;
  }
}

/**
 * Sends one host-plane request. Node's fetch sends no Origin header, which is
 * how the daemon tells Desktop apart from a web page; nothing here may add one.
 */
async function hostFetch(
  target: QaBrowserDaemonPort,
  path: string,
  secret: string,
  init: { method: string; body?: unknown },
): Promise<QaBrowserHostResult> {
  let response: Response;
  try {
    response = await fetch(`http://127.0.0.1:${target.port}${path}`, {
      method: init.method,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${secret}`,
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    return { ok: false, reason: "daemon_unavailable" };
  }
  if (response.status === 404 || response.status === 410) return { ok: false, reason: "session_ended" };
  if (response.status === 400) return { ok: false, reason: "invalid_request" };
  if (!response.ok) return { ok: false, reason: "daemon_unavailable" };

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, reason: "daemon_unavailable" };
  }
  if (!payload || typeof payload !== "object" || !("session" in payload)) {
    return { ok: false, reason: "daemon_unavailable" };
  }
  const session = parseQaBrowserSessionView(payload.session);
  if (!session) return { ok: false, reason: "daemon_unavailable" };
  return {
    ok: true,
    session,
    directive:
      "directive" in payload && typeof payload.directive === "string" ? payload.directive : undefined,
    commands: "commands" in payload ? readCommands(payload.commands) : undefined,
  };
}

function readCommands(value: unknown): QaBrowserHostCommand[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const commands: QaBrowserHostCommand[] = [];
  for (const entry of value) {
    if (!entry || typeof entry !== "object") continue;
    if (!("id" in entry) || typeof entry.id !== "string") continue;
    if (!("action" in entry) || typeof entry.action !== "string") continue;
    commands.push({
      id: entry.id,
      action: entry.action,
      params:
        "params" in entry && entry.params && typeof entry.params === "object"
          ? (entry.params as Record<string, unknown>)
          : undefined,
    });
  }
  return commands;
}

export function registerQaBrowserHost(
  target: QaBrowserDaemonPort,
  secret: string,
  request: {
    sessionId: string;
    taskId: string;
    windowId: string;
    cdpReady: boolean;
  },
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/host/register", secret, {
    method: "POST",
    body: {
      session_id: request.sessionId,
      task_id: request.taskId,
      window_id: request.windowId,
      cdp_ready: request.cdpReady,
    },
  });
}

export function heartbeatQaBrowserHost(
  target: QaBrowserDaemonPort,
  secret: string,
  request: {
    sessionId: string;
    seq: number;
    viewAlive: boolean;
    url?: { host: string; path: string };
    results?: QaBrowserHostCommandResult[];
  },
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/host/heartbeat", secret, {
    method: "POST",
    body: {
      session_id: request.sessionId,
      seq: request.seq,
      view_alive: request.viewAlive,
      url_host: request.url?.host,
      url_path: request.url?.path,
      results: request.results,
      long_poll_ms: LONG_POLL_MS,
    },
  });
}

export function ackQaBrowserSessionClosed(
  target: QaBrowserDaemonPort,
  secret: string,
  sessionId: string,
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/host/session/closed", secret, {
    method: "POST",
    body: { session_id: sessionId },
  });
}

export function takeoverQaBrowserSession(
  target: QaBrowserDaemonPort,
  secret: string,
  sessionId: string,
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/session/takeover", secret, {
    method: "POST",
    body: { session_id: sessionId },
  });
}

export function releaseQaBrowserSession(
  target: QaBrowserDaemonPort,
  secret: string,
  sessionId: string,
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/session/release", secret, {
    method: "POST",
    body: { session_id: sessionId },
  });
}

/** Opens (or reuses) the daemon-side session for the task Desktop is showing. */
export function openQaBrowserSession(
  target: QaBrowserDaemonPort,
  secret: string,
  request: { taskId: string; issueId: string; workspaceId?: string; agentId?: string },
): Promise<QaBrowserHostResult> {
  return hostFetch(target, "/browser/host/session", secret, {
    method: "POST",
    body: {
      task_id: request.taskId,
      issue_id: request.issueId,
      workspace_id: request.workspaceId,
      agent_id: request.agentId,
    },
  });
}

/**
 * Reads one session's state without touching liveness.
 *
 * The host plane's heartbeat is a long poll that also advances a sequence
 * number, so using it as a state read would make two writers share one
 * counter and let the card's 5 s poll starve the command loop. This is a plain
 * GET on the host discovery route, filtered to the one session asked about.
 */
export async function readQaBrowserSessionState(
  target: QaBrowserDaemonPort,
  secret: string,
  sessionId: string,
): Promise<QaBrowserHostResult> {
  let response: Response;
  try {
    response = await fetch(`http://127.0.0.1:${target.port}/browser/host/sessions`, {
      method: "GET",
      headers: { Authorization: `Bearer ${secret}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    return { ok: false, reason: "daemon_unavailable" };
  }
  if (response.status === 404) return { ok: false, reason: "session_ended" };
  if (!response.ok) return { ok: false, reason: "daemon_unavailable" };
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, reason: "daemon_unavailable" };
  }
  if (!payload || typeof payload !== "object" || !("sessions" in payload)) {
    return { ok: false, reason: "daemon_unavailable" };
  }
  const sessions = payload.sessions;
  if (!Array.isArray(sessions)) return { ok: false, reason: "daemon_unavailable" };
  const match = sessions.find(
    (entry) => !!entry && typeof entry === "object" && "session_id" in entry && entry.session_id === sessionId,
  );
  // Absent from the live list means the daemon already finalized it; that is
  // the same answer as a 404, so the card shows "ended" rather than a spinner.
  if (!match) return { ok: false, reason: "session_ended" };
  const session = parseQaBrowserSessionView(match);
  if (!session) return { ok: false, reason: "daemon_unavailable" };
  return { ok: true, session };
}

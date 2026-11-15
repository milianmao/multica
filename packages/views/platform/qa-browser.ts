// The QA built-in browser's renderer bridge (ORIO-7).
//
// Web has no host and no bridge, so every call reports `unsupported` rather
// than pretending: the sidebar card then renders its "desktop only" state
// instead of a button that does nothing.

/**
 * The daemon's stable session view (SDD §3.3), mirrored here rather than
 * imported from `apps/desktop`: `views` must not depend on an app, and the
 * shape is a wire contract either side can restate.
 *
 * Two fields are deliberately absent — a query string and a local path — so
 * there is nothing here that could carry a token or a filesystem location.
 */
export type QaBrowserSessionView = {
  session_id: string;
  task_id: string;
  issue_id: string;
  state: "requested" | "hosted" | "orphaned" | "closing" | "closed" | "failed";
  lock_owner: "none" | "agent" | "human";
  url: { host: string; path: string };
  partition: string;
  capability: "full" | "none";
  started_at: string;
  idle_expires_at?: string;
  hard_deadline?: string;
  last_evidence?: { attachment_id: string; captured_at: string; viewport_only: boolean };
  error?: { code: string; message: string };
};

export type QaBrowserBridgeResult<T> =
  | { ok: true; value: T }
  | { ok: false; reason: "unsupported" | "failed"; message?: string };

/** Shape of the preload bridge; the desktop app exposes it as `window.qaBrowser`. */
interface QaBrowserBridge {
  open(request: { taskId: string; issueId: string }): Promise<unknown>;
  getSessionState(): Promise<unknown>;
  setVisible(request: { sessionId: string; visible: boolean }): void;
  setBounds(request: {
    sessionId: string;
    rect: { x: number; y: number; width: number; height: number };
  }): void;
  takeover(request: { sessionId: string }): Promise<unknown>;
  release(request: { sessionId: string }): Promise<unknown>;
  captureEvidence(request: { sessionId: string }): Promise<unknown>;
}

const STATES: Record<string, true> = {
  requested: true,
  hosted: true,
  orphaned: true,
  closing: true,
  closed: true,
  failed: true,
};
const LOCKS: Record<string, true> = { none: true, agent: true, human: true };

function readBridge(): QaBrowserBridge | undefined {
  if (typeof window === "undefined") return undefined;
  const candidate = (window as unknown as { qaBrowser?: QaBrowserBridge }).qaBrowser;
  return candidate && typeof candidate.open === "function" ? candidate : undefined;
}

/**
 * Parses a session view off `unknown` IPC output. Unknown states read as
 * `orphaned` and an unreadable capability as `none`: both fall back to the
 * conservative answer rather than rendering an entry that cannot work.
 */
export function parseSessionView(value: unknown): QaBrowserSessionView | null {
  if (!value || typeof value !== "object") return null;
  if (!("session" in value)) return null;
  const session = value.session;
  if (!session || typeof session !== "object") return null;
  if (!("session_id" in session) || typeof session.session_id !== "string") return null;
  const state = "state" in session && typeof session.state === "string" ? session.state : "";
  const lock = "lock_owner" in session && typeof session.lock_owner === "string" ? session.lock_owner : "";
  const url = "url" in session && session.url && typeof session.url === "object" ? session.url : null;
  const host = url && "host" in url && typeof url.host === "string" ? url.host : "";
  const path = url && "path" in url && typeof url.path === "string" ? url.path : "";
  const evidence = "last_evidence" in session ? session.last_evidence : null;
  const error = "error" in session ? session.error : null;
  return {
    session_id: session.session_id,
    task_id: "task_id" in session && typeof session.task_id === "string" ? session.task_id : "",
    issue_id: "issue_id" in session && typeof session.issue_id === "string" ? session.issue_id : "",
    state: (STATES[state] === true ? state : "orphaned") as QaBrowserSessionView["state"],
    lock_owner: (LOCKS[lock] === true ? lock : "none") as QaBrowserSessionView["lock_owner"],
    url: { host, path },
    partition: "partition" in session && typeof session.partition === "string" ? session.partition : "",
    capability:
      "capability" in session && session.capability === "full" ? "full" : "none",
    started_at: "started_at" in session && typeof session.started_at === "string" ? session.started_at : "",
    idle_expires_at:
      "idle_expires_at" in session && typeof session.idle_expires_at === "string"
        ? session.idle_expires_at
        : undefined,
    hard_deadline:
      "hard_deadline" in session && typeof session.hard_deadline === "string"
        ? session.hard_deadline
        : undefined,
    last_evidence:
      evidence &&
      typeof evidence === "object" &&
      "attachment_id" in evidence &&
      typeof evidence.attachment_id === "string"
        ? {
            attachment_id: evidence.attachment_id,
            captured_at:
              "captured_at" in evidence && typeof evidence.captured_at === "string"
                ? evidence.captured_at
                : "",
            viewport_only: "viewport_only" in evidence && evidence.viewport_only === true,
          }
        : undefined,
    error:
      error && typeof error === "object" && "code" in error && typeof error.code === "string"
        ? {
            code: error.code,
            message: "message" in error && typeof error.message === "string" ? error.message : "",
          }
        : undefined,
  };
}

/** True in a Desktop build that can host a QA view. */
export function canUseQaBrowser(): boolean {
  return readBridge() !== undefined;
}

export async function openQaBrowserSession(
  taskId: string,
  issueId: string,
): Promise<QaBrowserSessionView | null> {
  const bridge = readBridge();
  if (!bridge) return null;
  try {
    return parseSessionView(await bridge.open({ taskId, issueId }));
  } catch {
    return null;
  }
}

export async function readQaBrowserSession(): Promise<QaBrowserSessionView | null> {
  const bridge = readBridge();
  if (!bridge) return null;
  try {
    return parseSessionView(await bridge.getSessionState());
  } catch {
    return null;
  }
}

export async function takeoverQaBrowserSession(sessionId: string): Promise<QaBrowserSessionView | null> {
  const bridge = readBridge();
  if (!bridge) return null;
  try {
    return parseSessionView(await bridge.takeover({ sessionId }));
  } catch {
    return null;
  }
}

export async function releaseQaBrowserSession(sessionId: string): Promise<QaBrowserSessionView | null> {
  const bridge = readBridge();
  if (!bridge) return null;
  try {
    return parseSessionView(await bridge.release({ sessionId }));
  } catch {
    return null;
  }
}

export async function captureQaBrowserEvidence(sessionId: string): Promise<{ ok: boolean }> {
  const bridge = readBridge();
  if (!bridge) return { ok: false };
  try {
    const result: unknown = await bridge.captureEvidence({ sessionId });
    return { ok: isOkResult(result) };
  } catch {
    return { ok: false };
  }
}

function isOkResult(value: unknown): boolean {
  return !!value && typeof value === "object" && "ok" in value && value.ok === true;
}

export function setQaBrowserVisible(sessionId: string, visible: boolean): void {
  readBridge()?.setVisible({ sessionId, visible });
}

export function setQaBrowserBounds(
  sessionId: string,
  rect: { x: number; y: number; width: number; height: number },
): void {
  readBridge()?.setBounds({ sessionId, rect });
}

/**
 * Binds the platform bridge to the shape the sidebar card consumes.
 *
 * Keeping the binding here rather than in the component means the component is
 * testable with a plain object, and Web — where no bridge exists — gets the
 * same "unsupported" behaviour instead of a second code path.
 */
export function createQaBrowserActions(): {
  open(taskId: string, issueId: string): Promise<QaBrowserSessionView | null>;
  read(): Promise<QaBrowserSessionView | null>;
  takeover(sessionId: string): Promise<QaBrowserSessionView | null>;
  release(sessionId: string): Promise<QaBrowserSessionView | null>;
  captureEvidence(sessionId: string): Promise<{ ok: boolean }>;
  setVisible(sessionId: string, visible: boolean): void;
} {
  return {
    open: (taskId, issueId) => openQaBrowserSession(taskId, issueId),
    read: () => readQaBrowserSession(),
    takeover: (sessionId) => takeoverQaBrowserSession(sessionId),
    release: (sessionId) => releaseQaBrowserSession(sessionId),
    captureEvidence: (sessionId) => captureQaBrowserEvidence(sessionId),
    setVisible: (sessionId, visible) => setQaBrowserVisible(sessionId, visible),
  };
}

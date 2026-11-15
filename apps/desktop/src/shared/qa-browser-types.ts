/**
 * The daemon's stable session view, as Desktop sees it (SDD §3.3).
 *
 * Two fields are deliberately absent: a query string and any local path. The
 * URL is host+path only because a query may carry a one-time token, and the
 * evidence is an attachment id because a local path is meaningless to anyone
 * reading the issue.
 */
export type QaBrowserSessionState =
  | "requested"
  | "hosted"
  | "orphaned"
  | "closing"
  | "closed"
  | "failed";

export type QaBrowserLockOwner = "none" | "agent" | "human";

export type QaBrowserCapability = "full" | "none";

export type QaBrowserSessionView = {
  session_id: string;
  task_id: string;
  issue_id: string;
  state: QaBrowserSessionState;
  lock_owner: QaBrowserLockOwner;
  url: { host: string; path: string };
  partition: string;
  capability: QaBrowserCapability;
  started_at: string;
  idle_expires_at?: string;
  hard_deadline?: string;
  last_evidence?: {
    attachment_id: string;
    captured_at: string;
    viewport_only: boolean;
  };
  error?: { code: string; message: string };
};

const STATES: Record<string, true> = {
  requested: true,
  hosted: true,
  orphaned: true,
  closing: true,
  closed: true,
  failed: true,
};
const LOCKS: Record<string, true> = { none: true, agent: true, human: true };

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * Parses a session view off the wire. Unknown states fall back to the most
 * conservative reading (`orphaned`) rather than failing: an installed Desktop
 * may outlive a daemon that adds a state, and a card that renders "unavailable"
 * is better than a crash in the sidebar.
 */
export function parseQaBrowserSessionView(value: unknown): QaBrowserSessionView | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const sessionId = readString(input.session_id);
  if (!sessionId) return null;
  const rawState = readString(input.state);
  const rawLock = readString(input.lock_owner);
  const url = (input.url ?? {}) as Record<string, unknown>;
  const evidence = input.last_evidence as Record<string, unknown> | undefined;
  const error = input.error as Record<string, unknown> | undefined;
  return {
    session_id: sessionId,
    task_id: readString(input.task_id),
    issue_id: readString(input.issue_id),
    state: (STATES[rawState] === true ? rawState : "orphaned") as QaBrowserSessionState,
    lock_owner: (LOCKS[rawLock] === true ? rawLock : "none") as QaBrowserLockOwner,
    url: { host: readString(url.host), path: readString(url.path) },
    partition: readString(input.partition),
    // An unreadable capability reads as "none": offering an entry whose tools
    // do not exist is the failure mode this field exists to prevent.
    capability: input.capability === "full" ? "full" : "none",
    started_at: readString(input.started_at),
    idle_expires_at: typeof input.idle_expires_at === "string" ? input.idle_expires_at : undefined,
    hard_deadline: typeof input.hard_deadline === "string" ? input.hard_deadline : undefined,
    last_evidence:
      evidence && typeof evidence.attachment_id === "string"
        ? {
            attachment_id: evidence.attachment_id,
            captured_at: readString(evidence.captured_at),
            viewport_only: evidence.viewport_only === true,
          }
        : undefined,
    error:
      error && typeof error.code === "string"
        ? { code: error.code, message: readString(error.message) }
        : undefined,
  };
}

/**
 * QA built-in isolated browser: the renderer↔main contract (SDD §4.4).
 *
 * Seven channels, one name each. `takeover` and `release` stay separate
 * channels rather than two branches of one: merging them makes "who dropped
 * the lock" unrecoverable from a log, which is exactly the question asked when
 * an agent and a person disagree about who had control.
 *
 * Everything here is pure: main-side wiring, preload, and the sidebar card all
 * import these parsers, so a payload the renderer can send is also a payload
 * main has already validated.
 */

import type { QaBrowserSessionView } from "./qa-browser-types";

export type { QaBrowserSessionView } from "./qa-browser-types";

export const QA_BROWSER_OPEN_CHANNEL = "qa-browser:open";
export const QA_BROWSER_SESSION_STATE_CHANNEL = "qa-browser:session-state";
export const QA_BROWSER_TOGGLE_CHANNEL = "qa-browser:toggle";
export const QA_BROWSER_BOUNDS_CHANNEL = "qa-browser:bounds";
export const QA_BROWSER_TAKEOVER_CHANNEL = "qa-browser:takeover";
export const QA_BROWSER_RELEASE_CHANNEL = "qa-browser:release";
export const QA_BROWSER_EVIDENCE_CHANNEL = "qa-browser:evidence";

/**
 * Every channel, as a value the IPC test drives its assertions from. A new
 * channel that skips this list is the one the test cannot see, so the list is
 * the single source of truth for "how many channels do we have".
 */
export const QA_BROWSER_CHANNELS = [
  QA_BROWSER_OPEN_CHANNEL,
  QA_BROWSER_SESSION_STATE_CHANNEL,
  QA_BROWSER_TOGGLE_CHANNEL,
  QA_BROWSER_BOUNDS_CHANNEL,
  QA_BROWSER_TAKEOVER_CHANNEL,
  QA_BROWSER_RELEASE_CHANNEL,
  QA_BROWSER_EVIDENCE_CHANNEL,
] as const;

export type QaBrowserChannel = (typeof QA_BROWSER_CHANNELS)[number];

export type QaBrowserOpenRequest = {
  taskId: string;
  issueId: string;
};

export type QaBrowserRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type QaBrowserToggleRequest = {
  sessionId: string;
  visible: boolean;
};

export type QaBrowserBoundsRequest = {
  sessionId: string;
  rect: QaBrowserRect;
};

export type QaBrowserSessionRequest = {
  sessionId: string;
};

export type QaBrowserActionResult =
  | { ok: true; session: QaBrowserSessionView }
  | { ok: false; reason: QaBrowserFailureReason; message?: string };

export type QaBrowserEvidenceResult =
  | { ok: true; attachmentId: string }
  | { ok: false; reason: QaBrowserFailureReason };

export type QaBrowserFailureReason =
  | "invalid_request"
  /** No Desktop host is attached to the daemon's view of this session. */
  | "no_host"
  /** The daemon could not be reached, or is older than this feature. */
  | "daemon_unavailable"
  | "session_ended"
  | "evidence_failed"
  /** The renderer named a session this window does not own. */
  | "not_this_window";

const MAX_ID_LENGTH = 128;

function readId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_ID_LENGTH) return null;
  return trimmed;
}

function readFinite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Validates the "open a browser for this task" request from the renderer. */
export function parseQaBrowserOpenRequest(value: unknown): QaBrowserOpenRequest | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const taskId = readId(input.taskId);
  const issueId = readId(input.issueId);
  if (!taskId || !issueId) return null;
  return { taskId, issueId };
}

export function parseQaBrowserToggleRequest(value: unknown): QaBrowserToggleRequest | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const sessionId = readId(input.sessionId);
  if (!sessionId || typeof input.visible !== "boolean") return null;
  return { sessionId, visible: input.visible };
}

/**
 * Geometry arrives in CSS px from `getBoundingClientRect`. Values are not
 * clamped here — main clamps to real bounds, because only it knows the window
 * content area and the zoom factor (SDD §6).
 */
export function parseQaBrowserBoundsRequest(value: unknown): QaBrowserBoundsRequest | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const sessionId = readId(input.sessionId);
  const rect = input.rect;
  if (!sessionId || !rect || typeof rect !== "object") return null;
  const raw = rect as Record<string, unknown>;
  const x = readFinite(raw.x);
  const y = readFinite(raw.y);
  const width = readFinite(raw.width);
  const height = readFinite(raw.height);
  if (x === null || y === null || width === null || height === null) return null;
  if (width < 0 || height < 0) return null;
  return { sessionId, rect: { x, y, width, height } };
}

export function parseQaBrowserSessionRequest(value: unknown): QaBrowserSessionRequest | null {
  if (!value || typeof value !== "object") return null;
  const input = value as Record<string, unknown>;
  const sessionId = readId(input.sessionId);
  if (!sessionId) return null;
  return { sessionId };
}

/**
 * CSS px → DIP. PoC measured 1000 DIP rendering as 667 CSS px at 1.5×, so the
 * conversion is `dip = cssPx × zoomFactor`, not the other way round.
 *
 * The result is clamped to `contentBounds` and rounded per edge. Rounding x/y
 * and width/height independently (rather than deriving x2/y2 and subtracting)
 * is what keeps the view aligned with its container: integer DIP rounding of a
 * right/bottom edge accumulates into a one-pixel jitter every frame.
 */
export function computeViewBounds(
  rect: QaBrowserRect,
  zoom: number,
  contentBounds: QaBrowserRect,
): QaBrowserRect {
  const factor = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const x = Math.round(rect.x * factor);
  const y = Math.round(rect.y * factor);
  const width = Math.round(rect.width * factor);
  const height = Math.round(rect.height * factor);

  const maxX = Math.round(contentBounds.x + contentBounds.width);
  const maxY = Math.round(contentBounds.y + contentBounds.height);
  const clampedX = Math.min(Math.max(x, Math.round(contentBounds.x)), maxX);
  const clampedY = Math.min(Math.max(y, Math.round(contentBounds.y)), maxY);
  const clampedRight = Math.min(Math.max(x + width, clampedX), maxX);
  const clampedBottom = Math.min(Math.max(y + height, clampedY), maxY);

  return {
    x: clampedX,
    y: clampedY,
    width: Math.max(0, clampedRight - clampedX),
    height: Math.max(0, clampedBottom - clampedY),
  };
}

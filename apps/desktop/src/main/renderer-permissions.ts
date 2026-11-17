import type { Session } from "electron";
import { isTrustedRendererURL } from "./navigation-guard";

/**
 * Permission policy for the renderer sessions that host Multica's own UI.
 *
 * Electron grants every permission request when no handler is registered, so a
 * missing policy means any script inside the renderer — which runs with
 * `webSecurity: false` (see renderer-web-preferences.ts) — can reach the
 * microphone, camera, screen capture and geolocation without an in-app prompt.
 * The allowlist is deliberately short, and every entry goes through the same
 * trusted-source test:
 *
 * - `media`, audio only. Video, screen capture and geolocation are denied.
 * - `clipboard-read`. Not a widening: Electron reports
 *   `navigator.clipboard.writeText()` through this permission, so denying it
 *   turns every Copy button in the app into a failure (verified against
 *   Electron 44: with a `media`-only handler, `writeText` rejects with
 *   NotAllowedError while `readText` succeeds once this is granted). The
 *   renderer already had clipboard access under Electron's default, so the
 *   policy narrows nothing here — it only stops the other permissions.
 *
 * Both hooks are required: most web APIs check first and only request when the
 * check fails, so a request-only policy leaves the check answering "granted".
 * The QA browser partition is unrelated and keeps its own deny-everything hooks
 * (qa-browser-host.ts).
 */

type RequestHandler = NonNullable<Parameters<Session["setPermissionRequestHandler"]>[0]>;
type CheckHandler = NonNullable<Parameters<Session["setPermissionCheckHandler"]>[0]>;

interface PermissionFields {
  /** Request hook: every media kind being asked for. */
  mediaTypes?: string[];
  /** Check hook: the single media kind being checked. */
  mediaType?: string;
  /** The frame's last load. Present for both hooks on a document request. */
  requestingUrl?: string;
  /** Chromium's fallback when no document URL is available. */
  securityOrigin?: string;
}

/**
 * Read the fields this policy needs off Electron's untyped details object.
 *
 * Both are optional in Electron's typings, so the shape is narrowed rather than
 * asserted: an unrecognisable request denies.
 */
function fieldsOf(details: unknown): PermissionFields {
  if (!details || typeof details !== "object") return {};
  const fields: PermissionFields = {};
  if ("mediaTypes" in details && Array.isArray(details.mediaTypes)) {
    fields.mediaTypes = details.mediaTypes.filter((type): type is string => typeof type === "string");
  }
  if ("mediaType" in details && typeof details.mediaType === "string") fields.mediaType = details.mediaType;
  if ("requestingUrl" in details && typeof details.requestingUrl === "string") {
    fields.requestingUrl = details.requestingUrl;
  }
  if ("securityOrigin" in details && typeof details.securityOrigin === "string") {
    fields.securityOrigin = details.securityOrigin;
  }
  return fields;
}

/**
 * Decide one permission request or check.
 *
 * `fallbackOrigin` is the check hook's own origin argument, used only when the
 * details carry no document URL. A request with no recognisable source denies,
 * so neither permission is ever granted unconditionally.
 */
function grantsPermission(
  permission: string,
  details: unknown,
  fallbackOrigin: string | undefined,
  trustedURL: string,
): boolean {
  if (permission !== "media" && permission !== "clipboard-read") return false;

  const fields = fieldsOf(details);
  const sourceURL = fields.requestingUrl || fields.securityOrigin || fallbackOrigin;
  if (!sourceURL || !isTrustedRendererURL(sourceURL, trustedURL)) return false;

  if (permission === "clipboard-read") return true;

  const mediaTypes = fields.mediaTypes ?? (fields.mediaType ? [fields.mediaType] : []);
  return mediaTypes.length > 0 && mediaTypes.every((type) => type === "audio");
}

/**
 * Install the policy on a session. Call before any window loads in it: a handler
 * registered later leaves the first loads unpoliced.
 */
export function installRendererPermissionPolicy(session: Session, trustedURL: string): void {
  const request: RequestHandler = (_contents, permission, callback, details) => {
    callback(grantsPermission(permission, details, undefined, trustedURL));
  };

  const check: CheckHandler = (_contents, permission, requestingOrigin, details) => {
    return grantsPermission(permission, details, requestingOrigin, trustedURL);
  };

  session.setPermissionRequestHandler(request);
  session.setPermissionCheckHandler(check);
}

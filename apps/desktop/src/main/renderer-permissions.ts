import type { Session } from "electron";
import { isTrustedRendererURL } from "./navigation-guard";

/**
 * Permission policy for the renderer sessions that host Multica's own UI.
 *
 * Electron grants every permission request when no handler is registered, so a
 * missing policy means any script inside the renderer — which runs with
 * `webSecurity: false` (see renderer-web-preferences.ts) — can reach the
 * microphone, camera, screen capture and geolocation without an in-app prompt.
 * Voice input is the only reason these sessions need a permission at all, so the
 * policy is an allowlist of exactly that: `media`, requested by the renderer
 * document itself, for `audio` only. Everything else is denied, including video.
 *
 * Both hooks are required: most web APIs check first and only request when the
 * check fails, so a request-only policy leaves the check answering "granted".
 * The QA browser partition is unrelated and keeps its own deny-everything hooks
 * (qa-browser-host.ts).
 */

type RequestHandler = NonNullable<Parameters<Session["setPermissionRequestHandler"]>[0]>;
type CheckHandler = NonNullable<Parameters<Session["setPermissionCheckHandler"]>[0]>;

/**
 * Decide a media permission from Electron's details object.
 *
 * The request hook reports `mediaTypes: ("audio" | "video")[]` plus the frame's
 * last URL; the check hook reports a single `mediaType` instead. Both fields are
 * optional in Electron's typings, so the shape is narrowed here rather than
 * asserted: an unrecognisable request denies.
 */
function grantsAudio(details: unknown, trustedURL: string): boolean {
  if (!details || typeof details !== "object") return false;

  const mediaTypes: string[] = [];
  if ("mediaTypes" in details && Array.isArray(details.mediaTypes)) {
    mediaTypes.push(...details.mediaTypes.filter((type): type is string => typeof type === "string"));
  }
  if ("mediaType" in details && typeof details.mediaType === "string") mediaTypes.push(details.mediaType);
  if (mediaTypes.length === 0 || mediaTypes.some((type) => type !== "audio")) return false;

  const sourceURL =
    ("requestingUrl" in details && typeof details.requestingUrl === "string" ? details.requestingUrl : undefined) ??
    ("securityOrigin" in details && typeof details.securityOrigin === "string" ? details.securityOrigin : undefined);
  if (!sourceURL) return false;
  return isTrustedRendererURL(sourceURL, trustedURL);
}

/**
 * Install the policy on a session. Call before any window loads in it: a handler
 * registered later leaves the first loads unpoliced.
 */
export function installRendererPermissionPolicy(session: Session, trustedURL: string): void {
  const request: RequestHandler = (_contents, permission, callback, details) => {
    callback(permission === "media" && grantsAudio(details, trustedURL));
  };

  const check: CheckHandler = (_contents, permission, _requestingOrigin, details) => {
    return permission === "media" && grantsAudio(details, trustedURL);
  };

  session.setPermissionRequestHandler(request);
  session.setPermissionCheckHandler(check);
}

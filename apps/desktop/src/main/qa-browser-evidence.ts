import { createHash } from "crypto";
import type { QaBrowserDaemonPort } from "./qa-browser-client";
import { readQaBrowserHostSecret } from "./qa-browser-client";

/**
 * QA built-in isolated browser: evidence upload (SDD §5).
 *
 * The PNG travels as the request body with its length and digest in the query.
 * It must never go through the JSON endpoints: 4 MiB of PNG is ~5.3 MiB once
 * base64-encoded, and the 64 KiB JSON cap would truncate it into a corrupt
 * image whose failure surfaces after decoding, looking like a transport fault.
 *
 * The artifact cap is enforced by the caller before this runs — Desktop main
 * owns 4 MiB, the daemon owns the 6 MiB transport limit, and the two must not
 * stand in for each other.
 */

const UPLOAD_TIMEOUT_MS = 60_000;

export type QaBrowserEvidenceUpload =
  | { ok: true; attachmentId: string }
  | { ok: false; reason: string };

export async function uploadQaBrowserEvidence(
  target: QaBrowserDaemonPort,
  sessionId: string,
  png: Buffer,
  meta: { urlHost: string; urlPath: string },
): Promise<QaBrowserEvidenceUpload> {
  const secret = await readQaBrowserHostSecret(target.profile);
  if (!secret) return { ok: false, reason: "daemon_unavailable" };

  const digest = createHash("sha256").update(png).digest("hex");
  const params = new URLSearchParams({
    session_id: sessionId,
    bytes: String(png.byteLength),
    sha256: digest,
    // URL metadata rides the query: it is data, and a header cannot carry the
    // non-ASCII a percent-encoded path may contain.
    url_host: meta.urlHost,
    url_path: meta.urlPath,
  });

  let response: Response;
  try {
    response = await fetch(
      `http://127.0.0.1:${target.port}/browser/host/evidence?${params.toString()}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "image/png",
          Authorization: `Bearer ${secret}`,
        },
        // A fixed-length body is mandatory here: the daemon answers chunked
        // evidence with 411, and a Uint8Array view gives fetch a definite
        // Content-Length. `new Uint8Array(png)` shares the bytes, so no copy.
        body: new Uint8Array(png),
        signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
      },
    );
  } catch {
    return { ok: false, reason: "daemon_unavailable" };
  }
  if (!response.ok) return { ok: false, reason: `evidence_rejected_${response.status}` };

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    return { ok: false, reason: "unreadable_response" };
  }
  if (!payload || typeof payload !== "object" || !("session" in payload)) {
    return { ok: false, reason: "unreadable_response" };
  }
  if (!payload.session || typeof payload.session !== "object") {
    return { ok: false, reason: "unreadable_response" };
  }
  if (!("last_evidence" in payload.session)) return { ok: false, reason: "no_evidence_recorded" };
  const evidence = payload.session.last_evidence;
  if (!evidence || typeof evidence !== "object" || !("attachment_id" in evidence)) {
    return { ok: false, reason: "no_evidence_recorded" };
  }
  const attachmentId = evidence.attachment_id;
  if (typeof attachmentId !== "string" || !attachmentId) {
    return { ok: false, reason: "no_evidence_recorded" };
  }
  return { ok: true, attachmentId };
}

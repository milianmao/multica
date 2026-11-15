/**
 * Redaction for text that comes out of the page under test.
 *
 * The daemon is the authoritative redactor — it owns `server/pkg/redact` and
 * runs every browser tool result through it before the text reaches the agent
 * or the transcript. This module is the Desktop-side twin, and it exists for a
 * narrower reason: the same string can end up in a main-process log line or a
 * crash dump before it is ever sent, and a credential in a crash dump is just as
 * leaked as one in a comment.
 *
 * The rule set is deliberately the console subset, not a general-purpose copy:
 * cookie headers, session pairs (`document.cookie` prints bare `name=value`),
 * bearer tokens and `mat_` task tokens. Anything broader belongs in the daemon,
 * where it has one owner.
 */

/** Header form: `Cookie: ...`, `"Cookie": "..."`, and tab-separated pairs. */
const COOKIE_HEADER = /\b(Set-Cookie|Cookie)["']?[ \t]*[:=][^\r\n]*/gi;
const COOKIE_HEADER_SPACE = /\b(Set-Cookie|Cookie)[ \t]+([A-Za-z0-9_.~-]+=)[^\r\n]*/gi;
/** Bare `name=value`, the shape `document.cookie` prints. */
const SESSION_PAIR = /\b((?:sid|session|sess|auth|access|refresh|id)[A-Za-z0-9_.-]*)[ \t]*=[ \t]*([A-Za-z0-9._~+/%-]+)/gi;
const BEARER = /\bBearer\s+[A-Za-z0-9\-._~+/]+=*/gi;
const TASK_TOKEN = /\bmat_[A-Za-z0-9_-]{20,}/g;

/**
 * True when a matched value looks like an opaque identifier rather than a word.
 *
 * Mirrors the daemon's floor: 12+ characters mixing letters and digits, or any
 * long hex run. Without it, `session=failed` in ordinary prose would be
 * rewritten, and a redactor that mangles readable text gets disabled.
 */
function looksLikeSessionValue(value: string): boolean {
  if (value.length >= 12 && /^[0-9a-f]+$/i.test(value)) return true;
  if (value.length < 12) return false;
  return /[0-9]/.test(value) && /[A-Za-z]/.test(value);
}

/** Redacts console text before it can reach a log, a dump, or a tool result. */
export function redactConsoleText(input: string): string {
  let out = input
    .replace(COOKIE_HEADER, "$1: [REDACTED COOKIE]")
    .replace(COOKIE_HEADER_SPACE, "$1: [REDACTED COOKIE]")
    .replace(BEARER, "Bearer [REDACTED]")
    .replace(TASK_TOKEN, "[REDACTED AGENT PASS]");
  out = out.replace(SESSION_PAIR, (match, name: string, value: string) =>
    looksLikeSessionValue(value) ? `${name}=[REDACTED COOKIE]` : match,
  );
  return out;
}

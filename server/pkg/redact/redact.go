// Package redact provides functions for detecting and masking secrets
// in agent output before it reaches the database or WebSocket broadcast.
package redact

import (
	"regexp"
	"strings"
)

// secretPattern pairs a compiled regex with its replacement text.
type secretPattern struct {
	re          *regexp.Regexp
	replacement string
}

// Patterns are checked in order; first match wins per position.
var patterns = []secretPattern{
	// AWS access key IDs (always start with AKIA)
	{regexp.MustCompile(`\bAKIA[0-9A-Z]{16}\b`), "[REDACTED AWS KEY]"},

	// AWS secret access keys (40 char base64-ish, preceded by a common separator)
	{regexp.MustCompile(`(?i)(?:aws_secret_access_key|secret_?access_?key)\s*[=:]\s*[A-Za-z0-9/+=]{40}`), "[REDACTED AWS SECRET]"},

	// PEM private keys (multi-line)
	{regexp.MustCompile(`(?s)-----BEGIN[A-Z\s]*PRIVATE KEY-----.*?-----END[A-Z\s]*PRIVATE KEY-----`), "[REDACTED PRIVATE KEY]"},

	// GitHub tokens (classic PAT, OAuth, user-to-server, server-to-server, refresh)
	{regexp.MustCompile(`\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,255}\b`), "[REDACTED GITHUB TOKEN]"},

	// GitHub fine-grained personal access tokens use the github_pat_ prefix,
	// which the classic ghp_/gho_/... pattern above does not cover. Without
	// this line a fine-grained PAT emitted in agent output leaks unredacted
	// to the database and WebSocket broadcast.
	{regexp.MustCompile(`\bgithub_pat_[A-Za-z0-9_]{20,255}\b`), "[REDACTED GITHUB TOKEN]"},

	// OpenAI / Anthropic API keys
	{regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{20,}\b`), "[REDACTED API KEY]"},

	// Slack bot/user/legacy tokens. The char class includes 'e' so the
	// newer xoxe- config/refresh tokens are covered alongside xoxb/p/o/r/a/s.
	{regexp.MustCompile(`\bxox[bporase]-[A-Za-z0-9\-]{10,}\b`), "[REDACTED SLACK TOKEN]"},

	// Slack app-level tokens use the xapp- prefix, which the xox*- rule above
	// does not match. Without this an app-level token echoed in agent output
	// leaks unredacted to the DB / WebSocket broadcast.
	{regexp.MustCompile(`\bxapp-[A-Za-z0-9-]{10,}\b`), "[REDACTED SLACK TOKEN]"},

	// GitLab personal access tokens
	{regexp.MustCompile(`\bglpat-[A-Za-z0-9_-]{20,}\b`), "[REDACTED GITLAB TOKEN]"},

	// Google API keys always start with the AIza prefix and are 39 chars total
	// (AIza + 35). Capture and restore the trailing delimiter so keys ending in
	// a non-word character such as '-' are still redacted.
	{regexp.MustCompile(`\bAIza[0-9A-Za-z_-]{35}([^0-9A-Za-z_-]|$)`), "[REDACTED GOOGLE API KEY]$1"},

	// Stripe secret / restricted live keys (sk_live_ / rk_live_). The sk-
	// rule above only matches the hyphen form used by OpenAI/Anthropic; Stripe
	// uses an underscore, so live keys are not covered without this. Publishable
	// keys (pk_live_) are intentionally excluded — they are not secret.
	{regexp.MustCompile(`\b(?:sk|rk)_live_[0-9A-Za-z]{16,}\b`), "[REDACTED STRIPE KEY]"},

	// JWT tokens (three base64url segments)
	{regexp.MustCompile(`\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b`), "[REDACTED JWT]"},

	// Generic "Bearer <token>" in output
	{regexp.MustCompile(`(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b`), "Bearer [REDACTED]"},

	// Session cookies in a header dump. A QA browser's console output routinely
	// carries `Cookie: session=...` / `Set-Cookie: ... HttpOnly`, and those
	// values are live session credentials for the page under test: a comment or
	// transcript containing one hands over the account. The header name is kept
	// so the reader still sees that a cookie existed.
	//
	// The value runs to the end of the line, or to the closing quote when the
	// header is embedded in a JSON dump: stopping at the first quote left the
	// tail of `Cookie: session=a"b` behind, and treating a tab as a separator
	// (it is not one) let `Cookie<TAB>session=…` through. Both are covered by
	// consuming the whole run, quotes included.
	// The value is consumed to the end of the line. A quote-terminated match
	// left the tail of `Cookie: session=a"b` behind (`…b`), which is a partial
	// credential still on the wire; over-redacting one line in a console dump
	// is the cheaper error.
	{regexp.MustCompile(`(?i)\b(Set-Cookie|Cookie)["']?[ \t]*[:=][^\r\n]*`), "$1: [REDACTED COOKIE]"},
	// A header whose separator is whitespace rather than a colon — a common
	// shape when a page logs the pairs it just read. Recognised by what follows
	// (`name=value`), so ordinary prose mentioning a cookie is left alone.
	{regexp.MustCompile(`(?i)\b(Set-Cookie|Cookie)[ \t]+([A-Za-z0-9_.~-]+=)[^\r\n]*`), "$1: [REDACTED COOKIE]"},

	// Task-scoped agent tokens (`mat_`). The daemon mints one per run and it
	// authenticates as that task; a browser console that prints it would leak a
	// credential into the issue. The prefix is distinctive enough to match
	// without a word boundary, since it can follow punctuation in JSON.
	//
	// The labelled form is matched with its own label. If only the token were
	// replaced, a leading `token=` would still match the generic
	// `NAME=value` rule below and rewrite the placeholder into
	// `[REDACTED CREDENTIAL] AGENT PASS]` — so the label is consumed here.
	// So is the placeholder's own wording: it avoids TOKEN/SECRET/KEY/CREDENTIAL
	// for the same reason.
	{regexp.MustCompile(`(?i)\b(?:token|auth[_-]?token|access[_-]?token|mat)[\"']?\s*[=:]\s*mat_[A-Za-z0-9_-]{20,}`), "[REDACTED AGENT PASS]"},
	{regexp.MustCompile(`\bmat_[A-Za-z0-9_-]{20,}`), "[REDACTED AGENT PASS]"},

	// Connection strings with embedded passwords
	{regexp.MustCompile(`(?i)(?:postgres|mysql|mongodb|redis|amqp)(?:ql)?://[^:\s]+:[^@\s]+@`), "[REDACTED CONNECTION STRING]@"},

	// Generic key=value patterns for common secret env var names
	{regexp.MustCompile(`(?i)(?:API_KEY|API_SECRET|SECRET_KEY|SECRET|ACCESS_TOKEN|AUTH_TOKEN|PRIVATE_KEY|DATABASE_URL|DB_PASSWORD|DB_URL|REDIS_URL|PASSWORD|TOKEN)\s*[=:]\s*\S+`), "[REDACTED CREDENTIAL]"},
}

// maxRedactDepth bounds the walk in redactValue. Tool inputs are decoded from
// daemon-supplied JSON, so nesting depth is attacker-influenced; without a
// bound a pathologically nested payload would recurse until the stack blows and
// take the process down. Real tool inputs nest a handful of levels at most, so
// this only ever trips on abuse.
const maxRedactDepth = 32

// depthLimitPlaceholder replaces anything below maxRedactDepth. Returning the
// raw value there would hand back an unscrubbed string, which is exactly what
// this package exists to prevent, so the fail-safe direction is to drop it.
const depthLimitPlaceholder = "[REDACTED DEPTH LIMIT]"

// InputMap returns a copy of m with every string value passed through Text,
// including strings nested inside maps and slices.
//
// The nested walk is load-bearing, not defensive tidying: providers record
// structured tool inputs, and Codex records a file edit as
// changes[]{path, diff, content}. A top-level-only pass leaves a credential
// inside a patch body — or the full contents of a deleted .env — untouched on
// its way to the database and the WebSocket broadcast.
func InputMap(m map[string]any) map[string]any {
	return redactMap(m, 0)
}

func redactMap(m map[string]any, depth int) map[string]any {
	if m == nil {
		return nil
	}
	if depth >= maxRedactDepth {
		return map[string]any{"_": depthLimitPlaceholder}
	}
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = redactValue(v, depth+1)
	}
	return out
}

// redactValue scrubs a single decoded JSON value, recursing through the
// composite shapes json.Unmarshal produces plus []string, which providers use
// for argv-style inputs.
//
// Composites are copied rather than scrubbed in place: the caller still holds
// the original map and keeps using it after redaction (the daemon handler logs
// and re-reads it), so mutating through the shared reference would be a
// surprise at a distance.
func redactValue(v any, depth int) any {
	if depth >= maxRedactDepth {
		return depthLimitPlaceholder
	}
	switch t := v.(type) {
	case string:
		return Text(t)
	case map[string]any:
		return redactMap(t, depth)
	case []any:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = redactValue(e, depth+1)
		}
		return out
	case []string:
		out := make([]string, len(t))
		for i, e := range t {
			out[i] = Text(e)
		}
		return out
	case map[string]string:
		out := make(map[string]string, len(t))
		for k, e := range t {
			out[k] = Text(e)
		}
		return out
	default:
		return v
	}
}

// Text scans the input string for known secret patterns and replaces
// matches with safe placeholders.
//
// It deliberately does NOT mask the local home directory. That masking used to
// live here, but it was never a boundary: anyone who can read a transcript
// already sees repository paths, file contents, commands and diffs, so hiding
// one path segment protected nothing while making paths unusable for copy-paste
// and debugging. It was also incoherent about whose home directory it hid —
// Content/Output are redacted in the server's ingest handler, so a hosted
// deployment matched them against the *server's* home, never the machine
// running the agent. Transcript visibility is an authorization concern and is
// handled at that layer, not by string replacement here.
func Text(s string) string {
	for _, p := range patterns {
		s = p.re.ReplaceAllString(s, p.replacement)
	}
	return s
}

// cookiePairPattern finds a bare `name=value` pair whose name looks like a
// session identifier. `document.cookie` prints exactly this shape — no header
// prefix at all — so a page that logs it would otherwise leak the session.
// The name whitelist deliberately stops at session-shaped names. An earlier
// version included a bare `id`, which matched ordinary markup in a console dump
// (`element id=submitButton12`) and rewrote a perfectly readable element id into
// a redaction marker. Over-redaction is not a leak, but it makes the console
// log useless to the person reading it, which is the same outcome by another
// route.
var cookiePairPattern = regexp.MustCompile(`(?i)\b((?:sid|session|sess|token|auth|access|refresh|csrf|xsrf)[A-Za-z0-9_.-]*)[ \t]*=[ \t]*([A-Za-z0-9._~+/%-]+)`)

// looksLikeSessionValue decides whether a matched value is an opaque identifier
// rather than an ordinary word.
//
// The floor exists because this rule is the ambiguous one: `session=failed` or
// `session=timeout` appear in perfectly readable prose, and a redactor that
// mangles normal text gets turned off by whoever has to read the output. So a
// value qualifies when it is pure hex of 12+ characters, or 12+ characters
// mixing letters and digits.
func looksLikeSessionValue(value string) bool {
	if isHexRun(value) {
		return true
	}
	if len(value) < 12 {
		return false
	}
	var hasDigit, hasLetter bool
	for _, r := range value {
		switch {
		case r >= '0' && r <= '9':
			hasDigit = true
		case (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z'):
			hasLetter = true
		}
	}
	return hasDigit && hasLetter
}

// ConsoleText is Text plus the session-pair rule, for browser console output
// only.
//
// TWIN IMPLEMENTATION: apps/desktop/src/main/qa-browser-redact.ts
// (`redactConsoleText`) reproduces this subset in TypeScript, because the same
// console line can reach a main-process log or crash dump before any request is
// made, and this side cannot retract that. The two are intentionally separate
// code — different languages, different processes — so they are kept in step by
// hand: changing cookiePairPattern, the value floor, or the cookie header rules
// HERE requires the same change THERE. SDD §14.9 lists the pairs. It is a separate entry point on purpose: `name=value` is far more
// ambiguous than a labelled credential, so it is applied where the source is
// known to be a page dumping its own state, not to every string the server
// persists.
func ConsoleText(s string) string {
	redacted := Text(s)
	return cookiePairPattern.ReplaceAllStringFunc(redacted, func(match string) string {
		eq := strings.IndexByte(match, '=')
		if eq <= 0 || !looksLikeSessionValue(strings.TrimSpace(match[eq+1:])) {
			return match
		}
		return match[:eq] + "=[REDACTED COOKIE]"
	})
}

// isHexRun reports whether the value is a long hexadecimal string.
func isHexRun(value string) bool {
	if len(value) < 12 {
		return false
	}
	for _, r := range value {
		switch {
		case r >= '0' && r <= '9':
		case r >= 'a' && r <= 'f':
		case r >= 'A' && r <= 'F':
		default:
			return false
		}
	}
	return true
}

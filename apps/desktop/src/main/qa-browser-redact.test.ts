import { describe, expect, it } from "vitest";
import { redactConsoleText } from "./qa-browser-redact";

/**
 * The Desktop twin of the daemon's console redactor.
 *
 * It exists because the same string can reach a main-process log line or a
 * crash dump before any HTTP request is made, and the daemon's pass cannot
 * retract that. The rules mirror `server/pkg/redact`; the cases here are the
 * security review's reproductions plus the one shape that matters most for a QA
 * browser — a page printing `document.cookie`, which has no header prefix.
 */
describe("redactConsoleText", () => {
  it("redacts a cookie header including an embedded quote", () => {
    const out = redactConsoleText(`Cookie: session=a"b`);
    expect(out).not.toContain('a"b');
    expect(out).toContain("[REDACTED COOKIE]");
  });

  it("redacts a tab-separated cookie pair", () => {
    expect(redactConsoleText("Cookie\tsession=tabsep")).not.toContain("tabsep");
  });

  it("redacts a JSON-quoted cookie header", () => {
    const out = redactConsoleText(`"Cookie": "session=abc"`);
    expect(out).not.toContain("session=abc");
  });

  it("redacts the bare pairs document.cookie prints", () => {
    const out = redactConsoleText("sid=SECRETVALUE0123456789; Path=/");
    expect(out).not.toContain("SECRETVALUE0123456789");
    expect(out).toContain("[REDACTED COOKIE]");
  });

  it("redacts bearer tokens and task tokens", () => {
    const out = redactConsoleText("Authorization: Bearer abc123def456 gh\nmat_abcdefghijklmnopqrstuvwxyz012345");
    expect(out).not.toContain("abc123def456");
    expect(out).not.toContain("mat_abcdefghijklmnopqrstuvwxyz012345");
  });

  it("leaves ordinary element ids alone", () => {
    // P2-11: a bare `id` name matched markup and rewrote a readable element id.
    for (const input of ["element id=submitButton12", "<div id=mainContent42>"]) {
      expect(redactConsoleText(input)).toBe(input);
    }
  });

  it("still redacts the session-shaped names", () => {
    for (const [input, leak] of [
      ["csrf=Xk9f2LmQ7pR4tW1s", "Xk9f2LmQ7pR4tW1s"],
      ["xsrf_token=Ab3dE6gH9jK2mN5p", "Ab3dE6gH9jK2mN5p"],
    ]) {
      expect(redactConsoleText(input)).not.toContain(leak);
    }
  });

  it("leaves readable prose alone", () => {
    // A redactor that mangles ordinary text gets disabled, and then it protects
    // nothing. `session=failed` is prose, not a credential.
    for (const input of ["session=failed", "the session=timeout happened", "Cookies are delicious"]) {
      expect(redactConsoleText(input)).toBe(input);
    }
  });
});

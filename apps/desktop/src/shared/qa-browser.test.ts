import { describe, expect, it } from "vitest";
import {
  computeViewBounds,
  parseQaBrowserBoundsRequest,
  parseQaBrowserOpenRequest,
  parseQaBrowserSessionRequest,
  parseQaBrowserToggleRequest,
  QA_BROWSER_CHANNELS,
} from "./qa-browser";

/**
 * The geometry rules are the ones that produce visible defects when wrong: a
 * view offset by one pixel, or a hidden window whose rectangle follows a
 * minimised window's meaningless bounds.
 *
 * The PoC measured 1000 DIP rendering as 667 CSS px at 1.5×, which is why the
 * conversion multiplies CSS px by the zoom factor.
 */
describe("computeViewBounds", () => {
  const content = { x: 0, y: 0, width: 1200, height: 900 };

  it("converts CSS px to DIP at each supported zoom", () => {
    const rect = { x: 10, y: 20, width: 400, height: 300 };
    expect(computeViewBounds(rect, 0.5, content)).toEqual({ x: 5, y: 10, width: 200, height: 150 });
    expect(computeViewBounds(rect, 1, content)).toEqual({ x: 10, y: 20, width: 400, height: 300 });
    expect(computeViewBounds(rect, 1.5, content)).toEqual({ x: 15, y: 30, width: 600, height: 450 });
    expect(computeViewBounds(rect, 2, content)).toEqual({ x: 20, y: 40, width: 800, height: 600 });
  });

  it("clamps a rectangle that overflows the content area", () => {
    const bounds = computeViewBounds(
      { x: 1000, y: 800, width: 900, height: 700 },
      1,
      content,
    );
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(content.width);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(content.height);
  });

  it("clamps a negative origin instead of producing a negative bound", () => {
    const bounds = computeViewBounds({ x: -50, y: -20, width: 200, height: 100 }, 1, content);
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.y).toBeGreaterThanOrEqual(0);
  });

  it("never derives the right edge by subtracting rounded corners", () => {
    // Rounding x and width independently must keep the view flush with its
    // container; deriving x2 = round(x2) and subtracting reintroduces the
    // one-pixel jitter the PoC measured.
    const bounds = computeViewBounds({ x: 0.4, y: 0.4, width: 333.3, height: 200.6 }, 1, content);
    expect(bounds.x).toBe(0);
    expect(bounds.width).toBe(333);
  });

  it("treats a non-positive zoom as 1 rather than producing an inverted rectangle", () => {
    expect(computeViewBounds({ x: 10, y: 10, width: 100, height: 100 }, 0, content)).toEqual({
      x: 10,
      y: 10,
      width: 100,
      height: 100,
    });
  });
});

/**
 * The seven channels are the contract; a test that drives its list from
 * `QA_BROWSER_CHANNELS` cannot silently miss an eighth.
 */
describe("qa-browser IPC channels", () => {
  it("exposes exactly the seven contract channels with unique names", () => {
    expect(QA_BROWSER_CHANNELS).toHaveLength(7);
    expect(new Set(QA_BROWSER_CHANNELS).size).toBe(7);
    for (const channel of QA_BROWSER_CHANNELS) {
      expect(channel.startsWith("qa-browser:")).toBe(true);
    }
  });

  it("keeps takeover and release as separate channels", () => {
    // Merging them would make "who released the lock" unrecoverable from logs.
    expect(QA_BROWSER_CHANNELS).toContain("qa-browser:takeover");
    expect(QA_BROWSER_CHANNELS).toContain("qa-browser:release");
  });
});

describe("renderer payload validation", () => {
  it("rejects an open request without both ids", () => {
    expect(parseQaBrowserOpenRequest({ taskId: "t1" })).toBeNull();
    expect(parseQaBrowserOpenRequest({ issueId: "i1" })).toBeNull();
    expect(parseQaBrowserOpenRequest({ taskId: "", issueId: "i1" })).toBeNull();
    expect(parseQaBrowserOpenRequest({ taskId: "t1", issueId: "i1" })).toEqual({
      taskId: "t1",
      issueId: "i1",
    });
  });

  it("rejects an oversized id", () => {
    expect(
      parseQaBrowserOpenRequest({ taskId: "t".repeat(129), issueId: "i1" }),
    ).toBeNull();
  });

  it("requires a boolean visibility flag", () => {
    expect(parseQaBrowserToggleRequest({ sessionId: "s1", visible: "yes" })).toBeNull();
    expect(parseQaBrowserToggleRequest({ sessionId: "s1", visible: true })).toEqual({
      sessionId: "s1",
      visible: true,
    });
  });

  it("rejects non-finite geometry rather than passing NaN to setBounds", () => {
    const rect = { x: Number.NaN, y: 0, width: 10, height: 10 };
    expect(parseQaBrowserBoundsRequest({ sessionId: "s1", rect })).toBeNull();
    expect(
      parseQaBrowserBoundsRequest({
        sessionId: "s1",
        rect: { x: 1, y: 2, width: 3, height: 4 },
      }),
    ).toEqual({ sessionId: "s1", rect: { x: 1, y: 2, width: 3, height: 4 } });
  });

  it("rejects a negative-size rectangle", () => {
    expect(
      parseQaBrowserBoundsRequest({
        sessionId: "s1",
        rect: { x: 0, y: 0, width: -10, height: 10 },
      }),
    ).toBeNull();
  });

  it("requires a session id for session-scoped actions", () => {
    expect(parseQaBrowserSessionRequest({})).toBeNull();
    expect(parseQaBrowserSessionRequest({ sessionId: "bs_1" })).toEqual({ sessionId: "bs_1" });
  });
});

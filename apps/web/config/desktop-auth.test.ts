// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  desktopAuthCallbackUrl,
  desktopAuthProtocol,
  desktopAuthProtocolFromState,
} from "./desktop-auth";

describe("desktop OAuth handoff", () => {
  it("keeps Local callbacks separate from installed Multica without changing normal desktop login", () => {
    const token = "handoff+jwt/token";
    const local = desktopAuthProtocol("desktop-local");
    const normal = desktopAuthProtocol("desktop");
    expect(local).toBe("multica-local");
    expect(normal).toBe("multica");
    expect(desktopAuthProtocolFromState("next:/workspace,platform:desktop-local")).toBe(local);
    expect(desktopAuthProtocolFromState("platform:desktop,next:/workspace")).toBe(normal);
    expect(desktopAuthCallbackUrl(local!, token)).toBe(
      "multica-local://auth/callback?token=handoff%2Bjwt%2Ftoken",
    );
    expect(desktopAuthCallbackUrl(normal!, token)).toBe(
      "multica://auth/callback?token=handoff%2Bjwt%2Ftoken",
    );
    // Browser-only login and arbitrary scheme names are not desktop handoffs.
    expect(desktopAuthProtocol("custom-scheme")).toBeNull();
    expect(desktopAuthProtocolFromState("next:/workspace")).toBeNull();
  });
});

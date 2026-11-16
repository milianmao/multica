// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { installRendererPermissionPolicy } from "./renderer-permissions";

const RENDERER = "file:///Applications/Multica.app/Contents/renderer/index.html";
const FOREIGN = "https://evil.example/page.html";

type RequestCallback = (granted: boolean) => void;
type RequestFn = (
  contents: unknown,
  permission: string,
  callback: RequestCallback,
  details: unknown,
) => void;
type CheckFn = (contents: unknown, permission: string, origin: string, details: unknown) => boolean;

function install(trustedURL = RENDERER) {
  const session = {
    setPermissionRequestHandler: vi.fn(),
    setPermissionCheckHandler: vi.fn(),
  };
  installRendererPermissionPolicy(session as unknown as Parameters<typeof installRendererPermissionPolicy>[0], trustedURL);
  const request = session.setPermissionRequestHandler.mock.calls[0]![0] as RequestFn;
  const check = session.setPermissionCheckHandler.mock.calls[0]![0] as CheckFn;
  return { session, request, check };
}

function requested(fn: RequestFn, permission: string, details: unknown): boolean {
  let granted: boolean | undefined;
  fn({}, permission, (value: boolean) => (granted = value), details);
  if (granted === undefined) throw new Error("handler never called its callback");
  return granted;
}

describe("installRendererPermissionPolicy", () => {
  it("registers both hooks, since an API checks before it requests", () => {
    const { session } = install();
    expect(session.setPermissionRequestHandler).toHaveBeenCalledTimes(1);
    expect(session.setPermissionCheckHandler).toHaveBeenCalledTimes(1);
  });

  it("grants audio media requested by the renderer document", () => {
    const { request, check } = install();
    const details = { mediaTypes: ["audio"], requestingUrl: RENDERER };

    expect(requested(request, "media", details)).toBe(true);
    expect(check({}, "media", "file://", { mediaType: "audio", requestingUrl: RENDERER })).toBe(true);
  });

  it("denies camera, screen capture and geolocation even from the renderer", () => {
    const { request, check } = install();

    expect(requested(request, "media", { mediaTypes: ["video"], requestingUrl: RENDERER })).toBe(false);
    // Voice input never asks for both, and a mixed request must not smuggle video in.
    expect(requested(request, "media", { mediaTypes: ["audio", "video"], requestingUrl: RENDERER })).toBe(false);
    expect(requested(request, "display-capture", { requestingUrl: RENDERER })).toBe(false);
    expect(requested(request, "geolocation", { requestingUrl: RENDERER })).toBe(false);
    expect(requested(request, "notifications", { requestingUrl: RENDERER })).toBe(false);
    expect(check({}, "geolocation", "file://", { requestingUrl: RENDERER })).toBe(false);
    expect(check({}, "media", "file://", { mediaType: "video", requestingUrl: RENDERER })).toBe(false);
  });

  it("denies the same audio request from a foreign document", () => {
    const { request, check } = install();

    expect(requested(request, "media", { mediaTypes: ["audio"], requestingUrl: FOREIGN })).toBe(false);
    // A file:// frame that is not the renderer entry point is not the renderer.
    expect(
      requested(request, "media", { mediaTypes: ["audio"], requestingUrl: "file:///tmp/notes.html" }),
    ).toBe(false);
    expect(check({}, "media", FOREIGN, { mediaType: "audio", requestingUrl: FOREIGN })).toBe(false);
  });

  it("denies a request whose media type or origin is missing", () => {
    const { request, check } = install();

    expect(requested(request, "media", { requestingUrl: RENDERER })).toBe(false);
    expect(requested(request, "media", { mediaTypes: ["audio"] })).toBe(false);
    expect(requested(request, "media", null)).toBe(false);
    expect(check({}, "media", "", {})).toBe(false);
  });

  it("falls back to the security origin when Chromium reports no requesting URL", () => {
    const { request } = install("http://localhost:5173/");

    expect(requested(request, "media", { mediaTypes: ["audio"], securityOrigin: "http://localhost:5173" })).toBe(true);
    expect(requested(request, "media", { mediaTypes: ["audio"], securityOrigin: "http://localhost:9999" })).toBe(false);
  });
});

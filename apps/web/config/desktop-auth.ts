export type DesktopAuthProtocol = "multica" | "multica-local";

/** Resolve only supported desktop platform markers into deep-link protocols. */
export function desktopAuthProtocol(
  platform: string | null,
): DesktopAuthProtocol | null {
  switch (platform) {
    case "desktop":
      return "multica";
    case "desktop-local":
      return "multica-local";
    default:
      return null;
  }
}

/** Recover the desktop destination carried through the Google OAuth state. */
export function desktopAuthProtocolFromState(
  state: string,
): DesktopAuthProtocol | null {
  const platformPart = state.split(",").find((part) => part.startsWith("platform:"));
  return desktopAuthProtocol(
    platformPart ? platformPart.slice("platform:".length) : null,
  );
}

/** Keep automatic handoff and manual reopening on the same desktop protocol. */
export function desktopAuthCallbackUrl(
  protocol: DesktopAuthProtocol,
  token: string,
): string {
  return `${protocol}://auth/callback?token=${encodeURIComponent(token)}`;
}

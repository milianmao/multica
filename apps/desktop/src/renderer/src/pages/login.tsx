import { LoginPage } from "@multica/views/auth";
import { DragStrip } from "@multica/views/platform";
import { MulticaIcon } from "@multica/ui/components/common/multica-icon";

function requireRuntimeAppUrl(): string {
  const runtimeConfig = window.desktopAPI.runtimeConfig;
  if (!runtimeConfig.ok) {
    throw new Error(
      "Invariant violated: DesktopLoginPage rendered before App accepted runtime config",
    );
  }
  return runtimeConfig.config.appUrl;
}

/** Launch browser OAuth with the return destination of this desktop build. */
export function DesktopLoginPage() {
  const webUrl = requireRuntimeAppUrl();
  /** Keep the browser handoff on this build's registered deep-link protocol. */
  const handleGoogleLogin = () => {
    const platform = window.desktopAPI.appProtocol === "multica-local"
      ? "desktop-local"
      : "desktop";
    window.desktopAPI.openExternal(
      `${webUrl}/login?platform=${platform}`,
    );
  };

  return (
    <div className="flex h-screen flex-col">
      <DragStrip />
      <LoginPage
        logo={<MulticaIcon mode="intro" size={48} />}
        onSuccess={() => {
          // Auth store update triggers AppContent re-render → shows DesktopShell.
          // Initial workspace navigation happens in routes.tsx via IndexRedirect.
        }}
        onGoogleLogin={handleGoogleLogin}
      />
    </div>
  );
}

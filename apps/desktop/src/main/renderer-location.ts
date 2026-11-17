import { join } from "path";
import { pathToFileURL } from "url";
import { is } from "@electron-toolkit/utils";

/**
 * Where the renderer document lives, and the URL that identifies it as trusted.
 *
 * Extracted so the window loader, the navigation guard and the IPC sender check
 * all derive trust from the same value: the dev server origin in development,
 * the packaged `index.html` file URL in production. Every main-process module is
 * bundled into `out/main`, so `__dirname` resolves identically at each call site.
 */
export function rendererEntryPath(): string {
  return join(__dirname, "../renderer/index.html");
}

export function rendererDocumentURL(): string {
  const devURL = process.env["ELECTRON_RENDERER_URL"];
  return is.dev && devURL ? devURL : pathToFileURL(rendererEntryPath()).toString();
}

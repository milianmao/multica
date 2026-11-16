import { isTrustedRendererURL } from "../navigation-guard";

/**
 * Sender gate for voice IPC.
 *
 * A channel registered with `ipcMain.handle` is reachable from every webContents
 * in the process — including a QA browser view pointed at an arbitrary page. The
 * voice channels expose the microphone transcription path and the on-device
 * recording history (list, audio bytes, delete), so each one must prove the
 * message came from Multica's own renderer document.
 *
 * `senderFrame` is null once the frame navigated or was destroyed, so a null
 * frame is a rejection rather than an unknown-but-allowed case.
 */

/** The slice of Electron's IpcMainInvokeEvent / IpcMainEvent this gate needs. */
export interface IpcSenderEvent {
  senderFrame?: { url?: string } | null;
}

export function isTrustedVoiceSender(event: IpcSenderEvent, trustedURL: string): boolean {
  const url = event.senderFrame?.url;
  if (!url) return false;
  return isTrustedRendererURL(url, trustedURL);
}

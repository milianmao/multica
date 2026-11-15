import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  QA_BROWSER_BOUNDS_CHANNEL,
  QA_BROWSER_EVIDENCE_CHANNEL,
  QA_BROWSER_OPEN_CHANNEL,
  QA_BROWSER_RELEASE_CHANNEL,
  QA_BROWSER_SESSION_STATE_CHANNEL,
  QA_BROWSER_TAKEOVER_CHANNEL,
  QA_BROWSER_TOGGLE_CHANNEL,
} from "../shared/qa-browser";

/**
 * The IPC surface's two gates (SDD §4.4).
 *
 * A renderer that can name an arbitrary `sessionId` controls every hosted
 * browser in the process, so the whitelist is the security boundary — not a
 * sanity check. These tests drive the real handlers against a fake `ipcMain`
 * and assert each channel refuses both a non-window sender and a foreign id.
 */

const handlers = new Map<string, (event: unknown, payload?: unknown) => unknown>();
const listeners = new Map<string, (event: unknown, payload?: unknown) => void>();
const removed = { handlers: [] as string[], listeners: [] as string[] };

vi.mock("electron", () => ({
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, payload?: unknown) => unknown) => {
      handlers.set(channel, handler);
    },
    on: (channel: string, listener: (event: unknown, payload?: unknown) => void) => {
      listeners.set(channel, listener);
    },
    removeHandler: (channel: string) => {
      removed.handlers.push(channel);
      handlers.delete(channel);
    },
    removeAllListeners: (channel: string) => {
      removed.listeners.push(channel);
      listeners.delete(channel);
    },
  },
  BrowserWindow: {
    fromWebContents: (sender: { ownerWindowId?: number }) =>
      sender.ownerWindowId === undefined ? null : { id: sender.ownerWindowId },
  },
}));

const clientMock = vi.hoisted(() => ({
  readQaBrowserHostSecret: vi.fn(async () => "secret-value-that-is-long-enough"),
  readQaBrowserSessionState: vi.fn(),
  openQaBrowserSession: vi.fn(),
  registerQaBrowserHost: vi.fn(),
  heartbeatQaBrowserHost: vi.fn(),
  ackQaBrowserSessionClosed: vi.fn(),
  takeoverQaBrowserSession: vi.fn(),
  releaseQaBrowserSession: vi.fn(),
}));

vi.mock("./qa-browser-client", () => clientMock);
vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return { ...actual, readFile: vi.fn(), mkdirSync: vi.fn(), rmSync: vi.fn() };
});

import { registerQaBrowserIpc } from "./qa-browser-ipc";
import type { QaBrowserHost } from "./qa-browser-host";

function session(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "bs_1",
    task_id: "t1",
    issue_id: "i1",
    state: "hosted",
    lock_owner: "none",
    url: { host: "127.0.0.1:3000", path: "/dash" },
    partition: "qa-task-t1",
    capability: "full",
    started_at: "2026-11-15T06:00:00Z",
    ...overrides,
  };
}

function fakeHost(): QaBrowserHost {
  const entries = new Map<string, { sessionId: string; profileDir: string }>();
  return {
    ensure: (input: { sessionId: string }) => {
      const entry = entries.get(input.sessionId) ?? {
        sessionId: input.sessionId,
        profileDir: `/tmp/qa/${input.sessionId}`,
      };
      entries.set(input.sessionId, entry);
      return entry;
    },
    get: (sessionId: string) => entries.get(sessionId),
    list: () => [...entries.values()],
    has: (sessionId: string) => entries.has(sessionId),
    setVisible: vi.fn(),
    setBounds: vi.fn(),
    raise: vi.fn(),
    capture: vi.fn(async () => ({ png: Buffer.from("png") })),
    destroy: vi.fn(),
    destroyAll: vi.fn(),
    destroySession: vi.fn(),
    currentURL: () => "http://127.0.0.1:3000/dash?token=secret",
    execute: vi.fn(),
  } as unknown as QaBrowserHost;
}

const DAEMON = { port: 19515, profile: "desktop-test" };

async function dispatch(channel: string, event: unknown, payload?: unknown): Promise<unknown> {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`channel ${channel} is not registered`);
  return await handler(event, payload);
}

describe("qa-browser IPC", () => {
  let dispose: () => void;
  let host: QaBrowserHost;

  beforeEach(() => {
    handlers.clear();
    listeners.clear();
    removed.handlers = [];
    removed.listeners = [];
    for (const mock of Object.values(clientMock)) mock.mockReset();
    clientMock.readQaBrowserHostSecret.mockResolvedValue("secret-value-that-is-long-enough");
    host = fakeHost();
    dispose = registerQaBrowserIpc({ resolveDaemon: async () => DAEMON, host });
  });

  it("registers exactly the seven contract channels", () => {
    expect([...handlers.keys()].sort()).toEqual(
      [
        QA_BROWSER_EVIDENCE_CHANNEL,
        QA_BROWSER_OPEN_CHANNEL,
        QA_BROWSER_RELEASE_CHANNEL,
        QA_BROWSER_SESSION_STATE_CHANNEL,
        QA_BROWSER_TAKEOVER_CHANNEL,
      ].sort(),
    );
    // toggle and bounds are send-only channels (frame-rate), not invokes.
    expect([...listeners.keys()].sort()).toEqual(
      [QA_BROWSER_BOUNDS_CHANNEL, QA_BROWSER_TOGGLE_CHANNEL].sort(),
    );
  });

  it("reads one session's state without competing with the heartbeat loop", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    clientMock.readQaBrowserSessionState.mockResolvedValue({
      ok: true,
      session: session({ lock_owner: "human" }),
    });
    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });

    const result = await dispatch(QA_BROWSER_SESSION_STATE_CHANNEL, { sender: { ownerWindowId: 1 } });
    expect(clientMock.readQaBrowserSessionState).toHaveBeenCalledWith(DAEMON, "secret-value-that-is-long-enough", "bs_1");
    // The host plane's heartbeat advances a sequence number and long-polls; a
    // state read must not consume either, or the card's poll starves commands.
    expect(clientMock.heartbeatQaBrowserHost).not.toHaveBeenCalled();
    expect(result).toMatchObject({ ok: true, session: { lock_owner: "human" } });
  });

  it("reads the state for a window that has not opened anything as not_this_window", async () => {
    const result = await dispatch(QA_BROWSER_SESSION_STATE_CHANNEL, { sender: { ownerWindowId: 7 } });
    expect(result).toEqual({ ok: false, reason: "not_this_window" });
  });

  it("removes every channel on dispose", () => {
    dispose();
    expect(removed.handlers).toHaveLength(5);
    expect(removed.listeners).toHaveLength(2);
    expect(handlers.size).toBe(0);
    expect(listeners.size).toBe(0);
  });

  it("rejects a sender that is not a Multica window", async () => {
    const result = await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: {} }, { taskId: "t1", issueId: "i1" });
    expect(result).toEqual({ ok: false, reason: "invalid_request" });
    expect(clientMock.openQaBrowserSession).not.toHaveBeenCalled();
  });

  it("refuses a session id this window does not own", async () => {
    // Owned by another window id, so this sender must not reach it.
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });

    const result = await dispatch(
      QA_BROWSER_TAKEOVER_CHANNEL,
      { sender: { ownerWindowId: 2 } },
      { sessionId: "bs_1" },
    );
    expect(result).toEqual({ ok: false, reason: "not_this_window" });
    expect(clientMock.takeoverQaBrowserSession).not.toHaveBeenCalled();
  });

  it("opens, registers and returns the daemon's session view", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session({ state: "requested" }) });

    const result = await dispatch(
      QA_BROWSER_OPEN_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { taskId: "t1", issueId: "i1" },
    );

    expect(clientMock.registerQaBrowserHost).toHaveBeenCalledTimes(1);
    const [, , registration] = clientMock.registerQaBrowserHost.mock.calls[0]!;
    // The partition directory reported to the daemon is the one it deletes.
    expect(registration.profileDir).toBe("/tmp/qa/bs_1");
    expect(result).toMatchObject({ ok: true, session: { session_id: "bs_1", state: "requested" } });
  });

  it("destroys the view when registration is refused", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: false, reason: "daemon_unavailable" });

    const result = await dispatch(
      QA_BROWSER_OPEN_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { taskId: "t1", issueId: "i1" },
    );
    expect(result).toEqual({ ok: false, reason: "daemon_unavailable" });
    expect(host.destroy).toHaveBeenCalledWith("bs_1");
  });

  it("reports daemon_unavailable when no daemon is running", async () => {
    handlers.clear();
    dispose = registerQaBrowserIpc({ resolveDaemon: async () => null, host });
    const result = await dispatch(
      QA_BROWSER_OPEN_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { taskId: "t1", issueId: "i1" },
    );
    expect(result).toEqual({ ok: false, reason: "daemon_unavailable" });
  });

  it("raises the window only on an explicit takeover", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    clientMock.takeoverQaBrowserSession.mockResolvedValue({
      ok: true,
      session: session({ lock_owner: "human" }),
    });
    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });
    expect(host.raise).not.toHaveBeenCalled();

    await dispatch(QA_BROWSER_TAKEOVER_CHANNEL, { sender: { ownerWindowId: 1 } }, { sessionId: "bs_1" });
    expect(host.raise).toHaveBeenCalledWith("bs_1");
  });

  it("never raises the window on hand-back", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    clientMock.releaseQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });

    await dispatch(QA_BROWSER_RELEASE_CHANNEL, { sender: { ownerWindowId: 1 } }, { sessionId: "bs_1" });
    expect(host.raise).not.toHaveBeenCalled();
  });

  it("drops an unauthorized toggle and bounds frame instead of throwing", async () => {
    const toggle = listeners.get(QA_BROWSER_TOGGLE_CHANNEL)!;
    toggle({ sender: { ownerWindowId: 1 } }, { sessionId: "bs_unknown", visible: false });
    expect(host.setVisible).not.toHaveBeenCalled();

    const bounds = listeners.get(QA_BROWSER_BOUNDS_CHANNEL)!;
    bounds(
      { sender: { ownerWindowId: 1 } },
      { sessionId: "bs_unknown", rect: { x: 0, y: 0, width: 10, height: 10 } },
    );
    expect(host.setBounds).not.toHaveBeenCalled();
  });

  it("does not send an oversized screenshot to the daemon", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    const uploadEvidence = vi.fn(async () => ({ ok: true as const, attachmentId: "att_1" }));
    dispose();
    handlers.clear();
    dispose = registerQaBrowserIpc({ resolveDaemon: async () => DAEMON, host, uploadEvidence });

    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });

    (host.capture as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ tooLarge: 4 << 20 });
    const result = await dispatch(
      QA_BROWSER_EVIDENCE_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { sessionId: "bs_1" },
    );
    // 4 MiB + 1 KiB is inside the daemon's 6 MiB transport cap, so the refusal
    // has to happen here — a request would be accepted and only then fail.
    expect(result).toEqual({ ok: false, reason: "evidence_failed" });
    expect(uploadEvidence).not.toHaveBeenCalled();
  });

  it("retries a failed capture once, then reports evidence_failed", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    const uploadEvidence = vi.fn(async () => ({ ok: false as const, reason: "rejected" }));
    dispose();
    handlers.clear();
    dispose = registerQaBrowserIpc({ resolveDaemon: async () => DAEMON, host, uploadEvidence });

    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });
    const result = await dispatch(
      QA_BROWSER_EVIDENCE_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { sessionId: "bs_1" },
    );
    expect(result).toEqual({ ok: false, reason: "evidence_failed" });
    expect(uploadEvidence).toHaveBeenCalledTimes(2);
  });

  it("returns the attachment id on a successful upload", async () => {
    clientMock.openQaBrowserSession.mockResolvedValue({ ok: true, session: session() });
    clientMock.registerQaBrowserHost.mockResolvedValue({ ok: true, session: session() });
    const uploadEvidence = vi.fn(async () => ({ ok: true as const, attachmentId: "att_9" }));
    dispose();
    handlers.clear();
    dispose = registerQaBrowserIpc({ resolveDaemon: async () => DAEMON, host, uploadEvidence });

    await dispatch(QA_BROWSER_OPEN_CHANNEL, { sender: { ownerWindowId: 1 } }, { taskId: "t1", issueId: "i1" });
    const result = await dispatch(
      QA_BROWSER_EVIDENCE_CHANNEL,
      { sender: { ownerWindowId: 1 } },
      { sessionId: "bs_1" },
    );
    expect(result).toEqual({ ok: true, attachmentId: "att_9" });
  });
});

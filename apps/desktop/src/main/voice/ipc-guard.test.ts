// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  VOICE_HISTORY_AUDIO_CHANNEL,
  VOICE_HISTORY_CLEAR_CHANNEL,
  VOICE_HISTORY_LIST_CHANNEL,
  VOICE_HISTORY_REMOVE_CHANNEL,
  VOICE_HISTORY_SAVE_CHANNEL,
  VOICE_MODEL_DELETE_CHANNEL,
  VOICE_MODELS_LIST_CHANNEL,
  VOICE_PREPARE_CHANNEL,
  VOICE_TRANSCRIBE_CHANNEL,
} from "../../shared/voice";

/**
 * Voice IPC sender gate.
 *
 * Every `voice:*` channel reaches the microphone path or the on-device
 * recording history, and `ipcMain.handle` is reachable from any webContents in
 * the process — a QA browser view included. These tests drive the registered
 * handlers with a forged `senderFrame` and assert the history surface never
 * answers, while the real renderer document still does.
 */

const RENDERER = "file:///Applications/Multica.app/Contents/renderer/index.html";
const FOREIGN = "https://evil.example/";

const handlers = new Map<string, (event: unknown, ...args: unknown[]) => unknown>();
const listeners = new Map<string, (event: unknown, ...args: unknown[]) => void>();

vi.mock("electron", () => ({
  app: { getPath: () => "/userData", getAppPath: () => "/app", on: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
  ipcMain: {
    handle: (channel: string, handler: (event: unknown, ...args: unknown[]) => unknown) => {
      handlers.set(channel, handler);
    },
    on: (channel: string, listener: (event: unknown, ...args: unknown[]) => void) => {
      listeners.set(channel, listener);
    },
  },
  net: { fetch: vi.fn() },
  utilityProcess: { fork: vi.fn() },
}));

vi.mock("../renderer-location", () => ({ rendererDocumentURL: () => RENDERER }));

const historyCalls = vi.hoisted(() => ({
  list: vi.fn(() => [{ id: "entry-1", createdAt: 1, durationMs: 1, status: "done", text: "secret" }]),
  audio: vi.fn(async () => ({ audio: new Uint8Array([1]), mimeType: "audio/webm" })),
  remove: vi.fn(async () => undefined),
  clear: vi.fn(async () => undefined),
  save: vi.fn(async () => ({ id: "entry-2", createdAt: 1, durationMs: 1, status: "transcribing", text: "" })),
  expire: vi.fn(async () => false),
  init: vi.fn(async () => undefined),
  update: vi.fn(async () => undefined),
  setRetention: vi.fn(async () => undefined),
}));

vi.mock("./history-store", () => ({
  MAX_HISTORY_AUDIO_BYTES: 16 * 1024 * 1024,
  VoiceHistoryStore: class {
    init = historyCalls.init;
    list = historyCalls.list;
    audio = historyCalls.audio;
    remove = historyCalls.remove;
    clear = historyCalls.clear;
    save = historyCalls.save;
    expire = historyCalls.expire;
    update = historyCalls.update;
    setRetention = historyCalls.setRetention;
  },
}));

const modelCalls = vi.hoisted(() => ({
  download: vi.fn(),
  cancel: vi.fn(),
  remove: vi.fn(async () => undefined),
  removeModel: vi.fn(),
  isReady: vi.fn(() => true),
  init: vi.fn(async () => undefined),
  modelDir: vi.fn((id: string) => `/models/${id}`),
  list: vi.fn(() => []),
}));

vi.mock("./model-store", () => ({
  ModelStore: class {
    init = modelCalls.init;
    download = modelCalls.download;
    cancel = modelCalls.cancel;
    remove = modelCalls.remove;
    isReady = modelCalls.isReady;
    modelDir = modelCalls.modelDir;
    list = modelCalls.list;
  },
}));

const engineCalls = vi.hoisted(() => ({
  available: vi.fn(() => false),
  prepare: vi.fn(),
  transcribe: vi.fn(async () => "text"),
  stop: vi.fn(),
}));

vi.mock("./engine", () => ({
  VoiceEngine: class {
    available = engineCalls.available;
    prepare = engineCalls.prepare;
    transcribe = engineCalls.transcribe;
    stop = engineCalls.stop;
  },
}));

vi.mock("./catalog", () => ({ findVoiceModel: (id: string) => (id === "small" ? { id: "small" } : undefined) }));
vi.mock("./worker?modulePath", () => ({ default: "/worker.js" }));
vi.mock("./worker-protocol", () => ({
  hotwordList: () => "",
  MAX_VOICE_SAMPLES: 1_000_000,
}));

async function setup() {
  // Dynamic on purpose: the vi.mock factories above must be registered before
  // the module under test evaluates, which a static import would prevent.
  const { setupVoiceInput } = await import("./index");
  setupVoiceInput();
  return { handlers, listeners };
}

function invoke(channel: string, senderURL: string, ...args: unknown[]): unknown {
  const handler = handlers.get(channel);
  if (!handler) throw new Error(`no handler for ${channel}`);
  return handler({ senderFrame: { url: senderURL } }, ...args);
}

/** `voice:prepare` is fire-and-forget (`ipcMain.on`), so it has no reply to assert. */
function emit(channel: string, senderURL: string, ...args: unknown[]): void {
  const listener = listeners.get(channel);
  if (!listener) throw new Error(`no listener for ${channel}`);
  listener({ senderFrame: { url: senderURL } }, ...args);
}

describe("voice IPC sender gate", () => {
  it("refuses every history read and write from a foreign frame", async () => {
    await setup();

    expect(await invoke(VOICE_HISTORY_LIST_CHANNEL, FOREIGN)).toEqual([]);
    expect(await invoke(VOICE_HISTORY_AUDIO_CHANNEL, FOREIGN, "entry-1")).toBeNull();
    expect(await invoke(VOICE_HISTORY_SAVE_CHANNEL, FOREIGN, {
      audio: new Uint8Array([1]),
      mimeType: "audio/webm",
      durationMs: 1,
    })).toBeNull();
    await invoke(VOICE_HISTORY_REMOVE_CHANNEL, FOREIGN, "entry-1");
    await invoke(VOICE_HISTORY_CLEAR_CHANNEL, FOREIGN);

    expect(historyCalls.list).not.toHaveBeenCalled();
    expect(historyCalls.audio).not.toHaveBeenCalled();
    expect(historyCalls.save).not.toHaveBeenCalled();
    expect(historyCalls.remove).not.toHaveBeenCalled();
    expect(historyCalls.clear).not.toHaveBeenCalled();
  });

  it("refuses the model and transcription channels from a foreign frame", async () => {
    await setup();

    expect(await invoke(VOICE_MODELS_LIST_CHANNEL, FOREIGN)).toBeNull();
    await invoke(VOICE_MODEL_DELETE_CHANNEL, FOREIGN, "small");
    // The gate returns before the handler's promise chain starts, so this asserts
    // synchronously that no scheduling happened at all.
    emit(VOICE_PREPARE_CHANNEL, FOREIGN, { modelId: "small" });
    expect(
      await invoke(VOICE_TRANSCRIBE_CHANNEL, FOREIGN, { modelId: "small", samples: new Float32Array(4) }),
    ).toEqual({ ok: false, reason: "failed", error: "invalid request" });

    expect(modelCalls.remove).not.toHaveBeenCalled();
    expect(modelCalls.cancel).not.toHaveBeenCalled();
    expect(modelCalls.download).not.toHaveBeenCalled();
    expect(engineCalls.prepare).not.toHaveBeenCalled();
    expect(engineCalls.transcribe).not.toHaveBeenCalled();
  });

  it("refuses a null sender frame, which is what a navigated frame becomes", async () => {
    await setup();

    const handler = handlers.get(VOICE_HISTORY_LIST_CHANNEL)!;
    expect(await handler({ senderFrame: null })).toEqual([]);
    expect(await handler({})).toEqual([]);
    expect(historyCalls.list).not.toHaveBeenCalled();
  });

  it("serves the renderer document itself", async () => {
    await setup();

    expect(await invoke(VOICE_HISTORY_LIST_CHANNEL, RENDERER)).toEqual([
      { id: "entry-1", createdAt: 1, durationMs: 1, status: "done", text: "secret" },
    ]);
    expect(await invoke(VOICE_HISTORY_AUDIO_CHANNEL, RENDERER, "entry-1")).toEqual({
      audio: new Uint8Array([1]),
      mimeType: "audio/webm",
    });
    expect(historyCalls.list).toHaveBeenCalled();
    expect(historyCalls.audio).toHaveBeenCalledWith("entry-1");
  });
});

// ORIO-7 stage-7 harness: real Electron + real daemon, one evidence chain.
//
// Run from the repository root:
//
//   node scripts/qa-browser-harness.mjs
//
// This is the only check that exercises the product code on both sides at
// once, which is what the security review required before R1/A4 can be called
// verified:
//
//   apps/desktop/src/main/qa-browser-host.ts  (the real module, bundled)
//     -> WebContentsView + capturePage()  ->  PNG
//     -> POST /browser/host/evidence      -> the real daemon mux
//   server/internal/daemon/browser_session_http.go
//     -> attachment id (recorded by the harness sink)
//
// Electron is driven by a generated main process that imports the real host
// module, so a regression in it fails here rather than in a mock.

import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { homedir } from "node:os";
import { mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..");
const results = [];
let failures = 0;
let stage = "boot";

function section(name) {
  stage = name;
  process.stdout.write(`\n--- ${name} ---\n`);
}

function record(step, ok, detail) {
  results.push({ step, ok, detail });
  if (!ok) failures += 1;
  process.stdout.write(
    `${ok ? "PASS" : "FAIL"}  ${step}${detail === undefined ? "" : "  " + JSON.stringify(detail)}\n`,
  );
}

const PAGE_HTML = `<!doctype html><html><head><meta charset="utf-8"><title>ORIO-7 harness</title>
<style>
 body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#fff;color:#111}
 header{padding:12px 16px;background:#eef;border-bottom:2px solid #99c;color:#123}
 #band{height:420px;background:linear-gradient(90deg,#d22,#2a2 40%,#22d 80%,#fff)}
</style></head><body>
<header>harness page — visible band below</header><div id="band"></div>
<script>document.cookie='harness_cookie=1; path=/'; console.info('harness ready');</script>
</body></html>`;

function startPageServer() {
  return new Promise((done) => {
    const server = createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(PAGE_HTML);
    });
    server.listen(0, "127.0.0.1", () => done(server));
  });
}

/** Starts the Go half and waits for its readiness line. */
function startDaemon() {
  const child = spawn("go", ["run", "./scripts/qa-browser-harness-daemon"], {
    cwd: resolve(repoRoot, "server"),
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString();
  });
  const ready = new Promise((done, fail) => {
    const timer = setTimeout(() => fail(new Error(`daemon start timed out\n${stderr}`)), 180_000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
      const match = stdout.match(/HARNESS_READY (\{.*\})/);
      if (match) {
        clearTimeout(timer);
        done(JSON.parse(match[1]));
      }
    });
    child.on("exit", (code) => {
      clearTimeout(timer);
      fail(new Error(`daemon exited ${code}\n${stdout}\n${stderr}`));
    });
  });
  return { child, ready };
}

/**
 * The Electron entry point.
 *
 * It imports the product host module rather than reimplementing it, installs a
 * real evidence sender pointed at the harness daemon, and writes its report to
 * a JSON file because Electron's stdout is not reliably flushed on exit here.
 */
function electronSource({ pageURL, daemon, stateFile }) {
  return `
import { app } from "electron";
import { writeFileSync } from "node:fs";
import { QaBrowserHost } from ${JSON.stringify(resolve(repoRoot, "apps/desktop/src/main/qa-browser-host.ts"))};
import { startQaBrowserHostLoop } from ${JSON.stringify(resolve(repoRoot, "apps/desktop/src/main/qa-browser-runtime.ts"))};

const REPORT = ${JSON.stringify(stateFile)};
const DAEMON = ${JSON.stringify(daemon)};
const PAGE = ${JSON.stringify(pageURL)};
const report = [];
function note(step, ok, detail) {
  report.push({ step, ok: !!ok, detail });
  process.stdout.write((ok ? "PASS  " : "FAIL  ") + step + (detail === undefined ? "" : "  " + JSON.stringify(detail)) + "\\n");
}

async function uploadEvidence(sessionId, png) {
  const digest = require("node:crypto").createHash("sha256").update(png).digest("hex");
  const query = new URLSearchParams({
    session_id: sessionId,
    bytes: String(png.length),
    sha256: digest,
    url_host: "127.0.0.1",
    url_path: "/harness",
  });
  const response = await fetch("http://127.0.0.1:" + DAEMON.port + "/browser/host/evidence?" + query.toString(), {
    method: "POST",
    headers: { "Content-Type": "image/png", Authorization: "Bearer " + DAEMON.secret },
    body: new Uint8Array(png),
  });
  if (!response.ok) return { ok: false, reason: "http_" + response.status };
  const payload = await response.json();
  const id = payload && payload.session && payload.session.last_evidence && payload.session.last_evidence.attachment_id;
  return id ? { ok: true, attachmentId: id } : { ok: false, reason: "no_attachment_id" };
}

app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  const host = new QaBrowserHost();
  host.setEvidenceSender(uploadEvidence);
  try {
    // Open the session on the daemon FIRST: the evidence endpoint refuses an
    // unknown session, and the daemon mints the id, so the view must be created
    // for that id rather than a client-chosen one.
    const openResponse = await fetch("http://127.0.0.1:" + DAEMON.port + "/browser/host/session", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + DAEMON.secret },
      body: JSON.stringify({ task_id: DAEMON.task_id, issue_id: DAEMON.issue, workspace_id: DAEMON.workspace }),
    });
    note("daemon opened a session for the task", openResponse.ok, { status: openResponse.status });
    const opened = await openResponse.json();
    const sessionId = opened && opened.session && opened.session.session_id;
    note("session id came from the daemon", typeof sessionId === "string" && sessionId.length > 0, { sessionId });
    const partition = opened && opened.session ? opened.session.partition : "qa-task-harness";

    const entry = host.ensure({ sessionId, taskId: DAEMON.task_id, issueId: DAEMON.issue, partition });
    note("real host module created a WebContentsView", !!entry.view.webContents.id, {
      webContentsId: entry.view.webContents.id,
      partition: entry.partition,
    });
    note("window took a capture surface without focus", entry.window.isFocused() === false, {
      visible: entry.window.isVisible(),
      focused: entry.window.isFocused(),
    });
    try {
      const probeImg = await entry.view.webContents.capturePage();
      note("surface available right after ensure", true, { empty: probeImg.isEmpty(), bytes: probeImg.toPNG().length });
    } catch (error) {
      note("surface available right after ensure", false, String((error && error.message) || error));
    }

    await entry.view.webContents.loadURL(PAGE);
    await new Promise((r) => setTimeout(r, 900));
    note("view loaded the harness page", String(entry.view.webContents.getURL()).startsWith("http://127.0.0.1:"), {
      url: entry.view.webContents.getURL(),
    });

    // The agent-facing tool, end to end. Capture runs BEFORE the navigation
    // test: a blocked navigation leaves the renderer mid-transition, and a
    // subsequent loadURL in the same view can stall (measured), which would
    // make this assertion about the wrong thing.
    await new Promise((r) => setTimeout(r, 400));
    let directCapture = null;
    const diag = {
      visible: entry.window.isVisible(),
      focused: entry.window.isFocused(),
      bounds: entry.view.getBounds ? entry.view.getBounds() : null,
      url: entry.view.webContents.getURL(),
    };
    for (let attempt = 0; attempt < 3 && !(directCapture && directCapture.png); attempt += 1) {
      try {
        const img = await entry.view.webContents.capturePage();
        directCapture = img.isEmpty() ? { empty: true } : { png: img.toPNG() };
      } catch (error) {
        directCapture = { error: String((error && error.message) || error) };
      }
      if (!directCapture.png) await new Promise((r) => setTimeout(r, 600));
    }
    note("capture diagnostics", !!(directCapture && directCapture.png), { diag, directCapture: directCapture && directCapture.error });
    note("capturePage returned a PNG", !!(directCapture && "png" in directCapture), directCapture && "png" in directCapture
      ? { bytes: directCapture.png.length }
      : directCapture);
    if (directCapture && "png" in directCapture) {
      const colours = new Set();
      for (let i = 0; i < directCapture.png.length && colours.size < 512; i += 7) colours.add(directCapture.png[i]);
      note("captured image is not blank", colours.size > 8, { distinctByteValues: colours.size });
      const directUpload = await uploadEvidence(sessionId, directCapture.png);
      note("evidence upload accepted by the daemon", directUpload.ok === true, directUpload);
    }

    const registerResponse = await fetch("http://127.0.0.1:" + DAEMON.port + "/browser/host/register", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + DAEMON.secret },
      body: JSON.stringify({ session_id: sessionId, task_id: DAEMON.task_id, window_id: "harness", cdp_ready: true }),
    });
    note("daemon registered the host view", registerResponse.ok, { status: registerResponse.status });

    const shot = await host.execute(sessionId, { id: "shot-1", action: "browser_screenshot" });
    note("browser_screenshot succeeded", shot.ok === true, { text: shot.text, error: shot.error });
    note(
      "daemon returned an attachment id",
      !!(shot.result && typeof shot.result.attachment_id === "string" && shot.result.attachment_id.length > 0),
      { result: shot.result },
    );

    // P0-1 (new) — the session must survive being idle.
    //
    // The defect was structural: one heartbeat blocked in a long poll for as
    // long as the orphan threshold, so a quiet session declared itself dead.
    // Only the REAL loop can show this, which is why the previous harness (which
    // never started it) missed it entirely. Run it for 60s — 30 ticks at the
    // shipped 2s interval — and require the daemon to still report hosted.
    const loop = startQaBrowserHostLoop({
      host,
      resolveDaemon: async () => ({ port: DAEMON.port, profile: DAEMON.profile }),
      closeSession: async (_target, _secret, sid) => {
        note("daemon asked to close the view during the soak", true, { sid });
        host.destroy(sid);
      },
      executeCommands: (sid, commands) => Promise.all(commands.map((command) => host.execute(sid, command))),
      onDaemonLost: () => {},
      intervalMs: 2000,
    });
    const SOAK_MS = 60_000;
    const started = Date.now();
    let finalState = "";
    let heartbeats = 0;
    while (Date.now() - started < SOAK_MS) {
      await new Promise((r) => setTimeout(r, 5000));
      heartbeats += 1;
      const state = await host.execute(sessionId, { id: "probe-" + heartbeats, action: "browser_url" });
      const listed = await fetch("http://127.0.0.1:" + DAEMON.port + "/browser/host/sessions", {
        headers: { Authorization: "Bearer " + DAEMON.secret },
      }).then((r) => r.json());
      const entry = (listed.sessions || []).find((s) => s.session_id === sessionId);
      finalState = entry ? entry.state : "missing";
      if (!entry) {
        note("session disappeared during the soak", false, {
          atMs: Date.now() - started,
          sessions: (listed.sessions || []).map((x) => ({ id: x.session_id, state: x.state })),
        });
      }
      if (!state.ok) {
        note("session still usable during the soak", false, { at: Date.now() - started, error: state.error });
        break;
      }
    }
    loop.stop();
    note("real heartbeat loop ran for 60s", Date.now() - started >= SOAK_MS, { elapsedMs: Date.now() - started, checks: heartbeats });
    note("session still hosted after 60s of the real loop", finalState === "hosted", { finalState });

    // P0-2 (new) — a takeover must produce a window the person can see, and
    // that must not cost the screenshot chain.
    host.ensure({ sessionId, taskId: DAEMON.task_id, issueId: DAEMON.issue, partition: host.list()[0].partition });
    host.raise(sessionId);
    await new Promise((r) => setTimeout(r, 800));
    const raised = host.list()[0].window.getBounds();
    const area = require("electron").screen.getPrimaryDisplay().workArea;
    const inside =
      raised.x >= area.x &&
      raised.y >= area.y &&
      raised.x + raised.width <= area.x + area.width &&
      raised.y + raised.height <= area.y + area.height;
    note("takeover puts the window inside the work area", inside, { raised, area });
    const afterRaise = await host.capture(sessionId, 4 * 1024 * 1024);
    note("capture still works after the window is raised", !!(afterRaise && "png" in afterRaise),
      afterRaise && "png" in afterRaise ? { bytes: afterRaise.png.length } : afterRaise);

    // P2-6 in the real runtime, LAST. A page-initiated navigation is the only
    // form will-navigate gates; main's own loadURL bypasses it, which is why
    // browser_goto validates the scheme itself.
    try {
      await entry.view.webContents.executeJavaScript("window.location.href = 'file:///C:/Windows/win.ini'");
    } catch {
      // A rejected script is also a blocked navigation.
    }
    await new Promise((r) => setTimeout(r, 700));
    const afterFile = String(entry.view.webContents.getURL());
    note("page-initiated file: navigation is blocked", !afterFile.startsWith("file:"), { afterFile });

    writeFileSync(REPORT, JSON.stringify({
      report,
      attachmentId: shot.result && shot.result.attachment_id ? shot.result.attachment_id : null,
    }, null, 2));
  } catch (error) {
    writeFileSync(REPORT, JSON.stringify({ report, error: String((error && error.stack) || error) }, null, 2));
    note("harness completed without throwing", false, String(error));
  }
  app.exit(report.every((r) => r.ok) ? 0 : 1);
});
`;
}

async function main() {
  section("page server");
  const page = await startPageServer();
  const pageURL = `http://127.0.0.1:${page.address().port}/`;
  record("page server listening", true, { pageURL });

  section("daemon");
  const { child: daemonProcess, ready } = startDaemon();
  let daemon;
  try {
    daemon = await ready;
  } catch (error) {
    record("daemon started", false, String(error));
    page.close();
    daemonProcess.kill();
    return;
  }
  record("real daemon mux started", true, { port: daemon.port });

  section("electron");
  // The heartbeat loop resolves the host secret from the Desktop profile dir, so
  // the harness writes a real one. Using the real path (rather than injecting a
  // secret) is deliberate: it keeps `readQaBrowserHostSecret` in the tested
  // path instead of stubbing the very lookup that could regress.
  const profile = "desktop-harness";
  const profilePath = join(homedir(), ".multica", "profiles", profile);
  mkdirSync(profilePath, { recursive: true });
  writeFileSync(join(profilePath, "browser-host.secret"), `${daemon.secret}\n`, "utf8");

  const work = mkdtempSync(join(tmpdir(), "orio7-harness-"));
  const stateFile = join(work, "state.json");
  const entrySource = join(work, "entry.ts");
  const entryBundle = join(work, "main.cjs");
  writeFileSync(entrySource, electronSource({ pageURL, daemon: { ...daemon, profile }, stateFile }));
  await build({
    entryPoints: [entrySource],
    outfile: entryBundle,
    bundle: true,
    platform: "node",
    format: "cjs",
    external: ["electron"],
    logLevel: "silent",
  });
  record("harness entry bundled from the real host module", true);

  const electronPath = join(repoRoot, "node_modules/electron/dist/electron.exe");
  const run = await new Promise((done) => {
    const child = spawn(electronPath, [entryBundle], { cwd: repoRoot, stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    child.stdout.on("data", (chunk) => {
      out += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      out += chunk.toString();
    });
    const timer = setTimeout(() => child.kill(), 180_000);
    child.on("exit", (code) => {
      clearTimeout(timer);
      done({ code, out });
    });
  });
  if (run.out.trim()) process.stdout.write(`\n${run.out.trim()}\n`);

  let state = null;
  try {
    state = JSON.parse(readFileSync(stateFile, "utf8"));
  } catch {
    state = null;
  }
  record("electron exited cleanly", run.code === 0, { code: run.code });
  if (!state) {
    record("harness wrote a report", false, { stateFile });
  } else {
    for (const item of state.report || []) record(item.step, item.ok === true, item.detail);
    if (state.error) record("harness runtime error", false, state.error);
    if (state.attachmentId) {
      // The daemon's own record of what it stored, not the client's claim.
      const sink = await new Promise((done) => {
        fetch(`http://127.0.0.1:${daemon.port}/harness/evidence`)
          .then((response) => response.json())
          .then(done)
          .catch(() => done(null));
      });
      record("daemon stored evidence bytes", !!sink && sink.bytes > 0, sink);
      record("attachment id matches the daemon record", !!sink && sink.id === state.attachmentId, {
        client: state.attachmentId,
        daemon: sink && sink.id,
      });
    }
  }

  page.close();
  daemonProcess.kill();
  rmSync(work, { recursive: true, force: true });
  try {
    rmSync(profilePath, { recursive: true, force: true });
  } catch {
    // best effort
  }
}

main()
  .then(() => {
    const reportPath = resolve(here, "qa-browser-harness-report.json");
    writeFileSync(reportPath, JSON.stringify({ stage, results }, null, 2));
    process.stdout.write(`\nRESULT ${failures === 0 ? "PASS" : "FAIL"}  (report: ${reportPath})\n`);
    process.exit(failures === 0 ? 0 : 1);
  })
  .catch((error) => {
    record("harness", false, { stage, error: String((error && error.stack) || error) });
    process.stdout.write(`\nRESULT FAIL (${stage})\n`);
    process.exit(1);
  });

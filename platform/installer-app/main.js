// Trust Agent Setup — main process (spec 040, D-040-01/02/03/09).
//
// Organization-bound installer for the governed Hermes desktop agent. The
// per-tenant binding + branding are BAKED into resources/ at build time
// (scripts/build_desktop_installers.py): the employee installs and is bound
// to the organization with zero configuration.
//
// Flow: welcome → install (official Hermes + canonical bundle plugin + org
// binding + white-label skin + agent config) → embedded OIDC sign-in
// (authorization-code + PKCE S256, acr=2 — replaces trust-login on Windows,
// where the employee may have no Python) → done/launch.
//
// Headless modes (real functionality, used by E2E and admins):
//   TrustAgentSetup --install-only   run the install steps, log, exit
//   TrustAgentSetup --status         print the baked binding + resolved home
"use strict";

const {
  app,
  BrowserWindow,
  ipcMain,
  session,
} = require("electron");
const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

// --- Baked per-tenant configuration (D-040-02) -------------------------------

function resourcesDir() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "resources")
    : path.join(__dirname, "resources");
}

function loadJson(name) {
  const file = path.join(resourcesDir(), name);
  if (!fs.existsSync(file)) return null;
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

const BINDING = loadJson("org-binding.json");
const BRANDING = loadJson("branding.json") || {};
const PRODUCT = BRANDING.product_name || "Trust Agent";

if (!BINDING || !BINDING.slug) {
  // Fatal: a build without a binding must never pretend to work.
  console.error("FATAL: resources/org-binding.json missing or invalid — " +
    "this installer was built without an organization binding.");
  if (!process.argv.includes("--install-only") && !process.argv.includes("--status")) {
    app.whenReady().then(() => {
      const w = new BrowserWindow({ width: 520, height: 260 });
      w.loadURL("data:text/html," + encodeURIComponent(
        "<html><body style='font-family:sans-serif;padding:32px'>" +
        "<h2>Installer error</h2><p>This installer build is missing its " +
        "organization binding. Please re-download it from your organization's " +
        "download page.</p></body></html>"));
    });
  } else {
    process.exit(2);
  }
}

// --- Platform helpers ---------------------------------------------------------

const IS_WIN = process.platform === "win32";

function hermesHome() {
  // Windows native Hermes data dir: %LOCALAPPDATA%\hermes (Hermes
  // windows-native.md). POSIX: $HERMES_HOME or ~/.hermes (same as install.sh).
  if (IS_WIN) {
    return (
      process.env.TRUST_TA_HOME ||
      process.env.HERMES_HOME ||
      path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "hermes")
    );
  }
  return process.env.TRUST_TA_HOME || process.env.HERMES_HOME ||
    path.join(os.homedir(), ".hermes");
}

function run(cmd, args, opts = {}) {
  // Spawn helper resolving .cmd shims on Windows (hermes.cmd, npm.cmd...).
  // Children always see HERMES_HOME so `hermes config set` writes to the
  // home the wizard resolved (TRUST_TA_HOME override included).
  const env = { ...process.env, ...(opts.env || {}) };
  if (!env.HERMES_HOME) env.HERMES_HOME = hermesHome();
  return new Promise((resolve) => {
    const child = spawn(cmd, args, {
      shell: IS_WIN,
      windowsHide: true,
      ...opts,
      env,
    });
    let out = "";
    const feed = (d) => {
      out += d.toString();
      if (out.length > 8000) out = out.slice(-8000);
    };
    child.stdout && child.stdout.on("data", feed);
    child.stderr && child.stderr.on("data", feed);
    child.on("error", (e) => resolve({ code: -1, out: String(e) }));
    child.on("close", (code) => resolve({ code: code ?? -1, out }));
  });
}

async function detectHermes() {
  const probe = await run(IS_WIN ? "hermes.cmd" : "hermes", ["--version"]);
  if (probe.code === 0) return { found: true, version: probe.out.split("\n")[0] };
  // Fresh installs may not be on this process's PATH yet — look in the
  // canonical install locations before falling back to the official installer.
  const candidates = IS_WIN
    ? [path.join(process.env.LOCALAPPDATA || "", "hermes", "hermes.cmd"),
       path.join(process.env.LOCALAPPDATA || "", "hermes", "bin", "hermes.cmd")]
    : [path.join(os.homedir(), ".local", "bin", "hermes"),
       "/usr/local/bin/hermes"];
  for (const c of candidates) {
    if (c && fs.existsSync(c)) return { found: true, version: "existing", bin: c };
  }
  return { found: false };
}

function hermesBin() {
  return IS_WIN ? "hermes.cmd" : "hermes";
}

// --- Install steps (D-040-03/05, mirrors bundle install.sh — spec 023) --------

function logSend(win, line) {
  if (win) win.webContents.send("install:log", line);
  else console.log(line);
}

async function fetchBundle(workDir, log) {
  const url = `${BINDING.api_base}/download/${BINDING.slug}/bundle`;
  log(`==> Fetching the ${BINDING.slug} bundle (${url})`);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`bundle download failed: HTTP ${res.status}`);
  const expected = (res.headers.get("x-bundle-sha256") || "").toLowerCase();
  const body = Buffer.from(await res.arrayBuffer());
  const actual = crypto.createHash("sha256").update(body).digest("hex");
  if (expected && expected !== actual) {
    throw new Error(`bundle checksum mismatch (expected ${expected}, got ${actual})`);
  }
  const file = path.join(workDir, "bundle.tar.gz");
  fs.writeFileSync(file, body);
  log(`    bundle ok — sha256 ${actual.slice(0, 16)}…`);
  return file;
}

async function installSteps(win) {
  const log = (l) => logSend(win, l);
  const home = hermesHome();
  fs.mkdirSync(home, { recursive: true });
  log(`==> Installing the ${BINDING.slug} agent into ${home}`);

  // 1. Hermes itself — COMPLETE agent, official install per platform (D-040-01).
  let hermes = await detectHermes();
  if (!hermes.found) {
    log(`==> ${PRODUCT} not found — installing…`);
    if (IS_WIN) {
      const r = await run("powershell.exe", [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "& ([scriptblock]::Create((irm https://hermes-agent.nousresearch.com/install.ps1))) -NonInteractive",
      ]);
      if (r.code !== 0) throw new Error(`${PRODUCT} install failed:\n` + r.out);
    } else {
      const r = await run("bash", ["-c",
        "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"]);
      if (r.code !== 0) throw new Error(`${PRODUCT} install failed:\n` + r.out);
    }
    hermes = await detectHermes();
    if (!hermes.found) {
      throw new Error(
        `${PRODUCT} installed but not found on PATH. Open a new terminal and ` +
        "re-run this installer to continue.");
    }
  } else {
    log(`==> ${PRODUCT} found: ${hermes.version}`);
  }

  // 1b. trust-agent command alias (white-label, REQ-PE-003): the employee
  //     types trust-agent, not hermes. Wrapper alongside the binary — cero
  //     parches al core (constitution IV).
  const aliasDir = IS_WIN
    ? path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "hermes")
    : path.join(os.homedir(), ".local", "bin");
  fs.mkdirSync(aliasDir, { recursive: true });
  if (IS_WIN) {
    fs.writeFileSync(path.join(aliasDir, "trust-agent.cmd"),
      "@echo off\r\nhermes %*\r\n");
  } else {
    fs.writeFileSync(path.join(aliasDir, "trust-agent"),
      "#!/usr/bin/env bash\nexec hermes \"$@\"\n");
    try { fs.chmodSync(path.join(aliasDir, "trust-agent"), 0o755); } catch {}
  }
  log(`==> Command alias installed: trust-agent`);

  // 2. Canonical bundle = single source of the plugin + skin (D-040-03).
  const osTmp = os.tmpdir();
  const workDir = fs.mkdtempSync(path.join(osTmp, "trust-agent-setup-"));
  const bundleFile = await fetchBundle(workDir, log);
  const root = `trust-agent-${BINDING.slug}`;
  // bsdtar ships with Windows 10+; tar is universal on POSIX.
  const extract = await run("tar", [
    "-xzf", bundleFile, "-C", workDir,
    `${root}/plugin`, `${root}/skin.yaml`,
  ]);
  if (extract.code !== 0) throw new Error("bundle extraction failed:\n" + extract.out);

  // 3. Governance plugin (verbatim copy of the converged tree).
  log("==> Installing trust-agent-governance plugin");
  const pluginsDir = path.join(home, "plugins");
  fs.mkdirSync(pluginsDir, { recursive: true });
  const pluginDst = path.join(pluginsDir, "trust-agent-governance");
  fs.rmSync(pluginDst, { recursive: true, force: true });
  fs.cpSync(path.join(workDir, root, "plugin", "trust-agent-governance"), pluginDst,
    { recursive: true });
  const clean = await run(IS_WIN ? "cmd.exe" : "bash", IS_WIN
    ? ["/c", `dir /s /b "${pluginDst}\\__pycache__" 2>nul`]
    : ["-c", `find "${pluginDst}" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true`]);

  // 4. Organization binding (org_code is PUBLIC, D-023-02 — the security of
  //    enrollment is the admin approval, spec 007).
  log(`==> Binding this agent to organization ${BINDING.slug}`);
  const trustDir = path.join(home, "trust-agent");
  fs.mkdirSync(trustDir, { recursive: true });
  fs.writeFileSync(path.join(trustDir, "org-code.txt"), BINDING.slug + "\n");

  // 5. Environment: control plane + org code for the plugin's auto-enroll.
  const envFile = path.join(home, ".env");
  let envLines = fs.existsSync(envFile)
    ? fs.readFileSync(envFile, "utf8").split("\n").filter((l) => l &&
        !l.startsWith("TRUST_AGENT_API=") && !l.startsWith("TRUST_ORG_CODE="))
    : [];
  envLines.push(`TRUST_AGENT_API=${BINDING.api_base}`);
  envLines.push(`TRUST_ORG_CODE=${BINDING.slug}`);
  fs.writeFileSync(envFile, envLines.join("\n") + "\n");
  try { fs.chmodSync(envFile, 0o600); } catch { /* Windows */ }

  // 6. White-label skin from the organization's published branding.
  log(`==> Applying the ${BINDING.slug} white-label skin`);
  const skinsDir = path.join(home, "skins");
  fs.mkdirSync(skinsDir, { recursive: true });
  fs.copyFileSync(path.join(workDir, root, "skin.yaml"),
    path.join(skinsDir, `trust-${BINDING.slug}.yaml`));

  // 7. Profile config — via the safe writer, never a hand edit (invariant).
  log("==> Configuring the agent (gateway standalone, plugin, skin, model)");
  const skin = `trust-${BINDING.slug}`;
  const cfg = [
    ["gateway.standalone", "true"],
    ["plugins.enabled", '["trust-agent-governance"]'],
    ["platform_toolsets.cli", '["hermes-cli","trust_agent"]'],
    ["display.skin", skin],
    ["model.default", BINDING.model],
    ["model.provider", "custom"],
    ["model.base_url", BINDING.litellm_base_url],
    ["telemetry.shared_metrics.enabled", "false"],
  ];
  for (const [key, value] of cfg) {
    const r = await run(hermes.bin || hermesBin(), ["config", "set", key, value, "--force"]);
    if (r.code !== 0) throw new Error(`config set ${key} failed:\n${r.out}`);
  }

  fs.rmSync(workDir, { recursive: true, force: true });
  log("==> Installation complete — the agent is bound to " + BINDING.slug);
  return { home };
}

// --- Embedded OIDC sign-in (D-040-09, mirrors trust-login — spec 023) ---------

function b64url(buf) {
  return Buffer.from(buf).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function signInFlow(win) {
  const log = (l) => { win && win.webContents.send("signin:log", l); };
  const home = hermesHome();
  const redirectUri = "http://127.0.0.1:8765/callback";
  const verifier = b64url(crypto.randomBytes(48));
  const challenge = b64url(crypto.createHash("sha256").update(verifier).digest());
  const state = crypto.randomBytes(18).toString("base64url");
  const nonce = crypto.randomBytes(12).toString("base64url");
  const authUrl = `${BINDING.oidc_issuer_url}/protocol/openid-connect/auth?` +
    new URLSearchParams({
      response_type: "code",
      client_id: BINDING.oidc_client_id,
      redirect_uri: redirectUri,
      scope: "openid",
      state, nonce,
      code_challenge: challenge,
      code_challenge_method: "S256",
      acr_values: "2",
    });

  const result = await new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const u = new URL(req.url, "http://127.0.0.1");
      if (u.pathname !== "/callback") { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end("<html><body><h3>Sign-in complete.</h3>" +
        "<p>You can close this tab and return to the installer.</p>" +
        "<script>window.close()</script></body></html>");
      resolve({
        code: u.searchParams.get("code"),
        state: u.searchParams.get("state"),
        error: u.searchParams.get("error"),
      });
      server.close();
    });
    server.on("error", reject);
    server.listen(8765, "127.0.0.1", () => {
      log("Opening your organization's sign-in page…");
      const authWin = new BrowserWindow({
        parent: win, width: 980, height: 720, show: true,
        autoHideMenuBar: true,
        webPreferences: { session: session.fromPartition("trust-signin") },
      });
      authWin.loadURL(authUrl);
      authWin.on("closed", () => {
        // User closed the IdP window before finishing — abort cleanly.
        server.close();
        reject(new Error("Sign-in window closed before completing."));
      });
      server.on("close", () => { try { authWin.destroy(); } catch { /* already */ } });
    });
    setTimeout(() => { try { server.close(); } catch {} reject(new Error("Sign-in timed out (10 min).")); }, 600000);
  });

  if (result.error || !result.code) throw new Error("Identity provider error: " + result.error);
  if (result.state !== state) throw new Error("State mismatch — possible CSRF. Retry.");

  const tokenRes = await fetch(`${BINDING.oidc_issuer_url}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: BINDING.oidc_client_id,
      code: result.code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
    }),
  });
  if (!tokenRes.ok) throw new Error("token exchange failed: HTTP " + tokenRes.status);
  const tokens = await tokenRes.json();
  if (!tokens.id_token) throw new Error("The identity provider did not return an id_token.");

  const trustDir = path.join(home, "trust-agent");
  fs.mkdirSync(trustDir, { recursive: true });
  const tokenFile = path.join(trustDir, "id-token.txt");
  fs.writeFileSync(tokenFile, tokens.id_token);
  try { fs.chmodSync(tokenFile, 0o600); } catch { /* Windows */ }
  log(`Signed in. id_token saved to ${tokenFile}`);

  // Self-issue the model key (one-time) — same as trust-login --issue-key.
  const sess = await fetch(`${BINDING.api_base}/api/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id_token: tokens.id_token, kind: "desktop" }),
  });
  if (!sess.ok) throw new Error("session mint failed: HTTP " + sess.status);
  const sessBody = await sess.json();
  const sessionToken = sessBody.session_token || (sessBody.session || {}).session_token;
  if (!sessionToken) throw new Error("Session mint succeeded but no session_token.");
  const keyRes = await fetch(`${BINDING.api_base}/api/models/keys`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${sessionToken}`,
    },
    body: "{}",
  });
  if (!keyRes.ok) throw new Error("key issuance failed: HTTP " + keyRes.status);
  const keyBody = await keyRes.json();
  if (!keyBody.key) throw new Error("Key issuance succeeded but no key material.");
  const r = await run(hermesBin(), ["config", "set", "model.api_key", keyBody.key, "--force"]);
  if (r.code !== 0) throw new Error("config set model.api_key failed:\n" + r.out);
  log("Model key provisioned (shown exactly once) and stored in config.");
  return true;
}

// --- IPC + windows --------------------------------------------------------------

function createWindow() {
  const win = new BrowserWindow({
    width: 760,
    height: 600,
    resizable: true,
    autoHideMenuBar: true,
    title: (BRANDING.product_name || "Trust Agent") + " Setup",
    webPreferences: { preload: path.join(__dirname, "preload.js") },
  });
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  return win;
}

app.whenReady().then(async () => {
  if (process.argv.includes("--status")) {
    console.log(JSON.stringify({
      binding: BINDING,
      branding: { product_name: BRANDING.product_name, accent_color: BRANDING.accent_color },
      hermes_home: hermesHome(),
      hermes: await detectHermes(),
    }, null, 2));
    app.exit(0);
    return;
  }
  if (process.argv.includes("--install-only")) {
    try {
      await installSteps(null);
      console.log("INSTALL_OK");
      app.exit(0);
    } catch (e) {
      console.error("INSTALL_FAILED: " + (e && e.message ? e.message : e));
      app.exit(1);
    }
    return;
  }
  if (!BINDING) return; // fatal screen already shown

  const win = createWindow();
  ipcMain.handle("get-config", () => ({
    binding: {
      slug: BINDING.slug,
      api_base: BINDING.api_base,
      model: BINDING.model,
      plugin_version: BINDING.plugin_version,
    },
    branding: BRANDING,
    home: hermesHome(),
  }));
  ipcMain.handle("install", async () => {
    try {
      const out = await installSteps(win);
      return { ok: true, home: out.home };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  });
  ipcMain.handle("signin", async () => {
    try {
      await signInFlow(win);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  });
  ipcMain.handle("launch", async () => {
    // Best-effort: open a terminal running the governed agent.
    const cmd = IS_WIN ? "trust-agent.cmd" : "trust-agent";
    if (IS_WIN) {
      spawn("cmd.exe", ["/k", cmd], { detached: true, cwd: hermesHome() });
      return { ok: true };
    }
    for (const term of ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "alacritty", "xterm"]) {
      const r = await run("bash", ["-lc", `command -v ${term}`]);
      if (r.code === 0 && r.out.trim()) {
        spawn(term, ["-e", cmd], { detached: true, cwd: hermesHome() });
        return { ok: true };
      }
    }
    return { ok: false, hint: "Open a new terminal and run: trust-agent" };
  });
});

app.on("window-all-closed", () => app.quit());

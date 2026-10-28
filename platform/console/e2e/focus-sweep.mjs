/**
 * E2E focus sweep — t_90e899e0 (repro Miller, CRÍTICO).
 *
 * "cada vez que escribo en un box, escribo una letra y me saca del box
 *  y debo volver a clickear."
 *
 * Regresión de browser (nada simulado — stack real): escribe FRASES
 * completas en cada formulario de las 3 superficies (Admin Console,
 * auth panel del console y Provider Console) y afirma que NINGÚN input
 * pierde el foco por keystroke. La causa raíz era el efecto del Dialog
 * (deps inestables `[open, onClose]` + `setTimeout(...focus())` que robaba
 * el foco del input en cada re-render); este barrido cubre tanto inputs
 * de página como inputs dentro de TODOS los diálogos alcanzables de forma
 * segura (allowlist — nunca se hace submit ni delete).
 *
 * Cobertura:
 *   Console  : inputs de página en /overview /organization /departments
 *              /users /budgets /usage /models /policies /agents /devices
 *              /hermes-download /credentials /audit /branding + diálogos
 *              "Add department", "Rename", "Edit budget", "Edit" (budget),
 *              "Invite user", "New connection", "Add secret",
 *              "Approve model", "New policy", "+ Add item" (policies).
 *   Auth     : /login /register /signup (sin sesión).
 *   Provider : /login, /tenants/new, "Register provider" (dialog),
 *              "Delete tenant…" (confirm tipado — se escribe texto FALSO
 *              y se cierra con Escape: jamás se confirma).
 *
 * Uso (stack arriba):
 *   # sesión admin del console (baile real PKCE+TOTP, ver script):
 *   ../../platform/.venv/bin/python ../scripts/e2e_policies_ux.py \
 *       FocusSweep2026! /tmp/policies_ux_cookies.json
 *   CONSOLE_COOKIES=/tmp/policies_ux_cookies.json \
 *   PROVIDER_ADMIN_PASSWORD='...' \
 *   NODE_PATH=/usr/local/lib/hermes-agent/node_modules node e2e/focus-sweep.mjs
 *
 * Env: CONSOLE_BASE_URL (def http://127.0.0.1:3100),
 *      PROVIDER_BASE_URL (def http://127.0.0.1:3200),
 *      CONSOLE_COOKIES (JSON exportado por scripts/e2e_policies_ux.py),
 *      PROVIDER_ADMIN_PASSWORD (operador super-user, spec 025).
 */
import { createRequire } from "node:module";
import { readdirSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const CONSOLE_BASE = process.env.CONSOLE_BASE_URL ?? "http://127.0.0.1:3100";
const PROVIDER_BASE = process.env.PROVIDER_BASE_URL ?? "http://127.0.0.1:3200";
const COOKIES_FILE = process.env.CONSOLE_COOKIES;
const PROVIDER_PASSWORD = process.env.PROVIDER_ADMIN_PASSWORD;

/** Resolve `playwright` sin dependencia local (mismo patrón que
 *  provider-console/e2e/delete-panel.mjs). */
function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_NODE_PATH,
    "/usr/local/lib/hermes-agent/node_modules",
    ...(process.env.NODE_PATH ?? "").split(path.delimiter),
  ].filter(Boolean);
  for (const dir of candidates) {
    for (const name of ["playwright", "playwright-core"]) {
      try {
        return createRequire(path.join(dir, "index.js"))(name);
      } catch {
        try {
          return createRequire(import.meta.url)(name);
        } catch {
          /* siguiente candidato */
        }
      }
    }
  }
  throw new Error("playwright no encontrado (NODE_PATH=/usr/local/lib/hermes-agent/node_modules)");
}
const { chromium } = loadPlaywright();

/** Chromium cacheado más reciente (fallback agnóstico de versión — mismo
 *  patrón que provider-console/e2e/delete-panel.mjs). */
function chromiumExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  const root = path.join(homedir(), ".cache", "ms-playwright");
  try {
    const dirs = readdirSync(root)
      .filter((d) => /^chromium-\d+$/.test(d))
      .sort();
    for (const d of dirs.reverse()) {
      for (const bin of [
        path.join(root, d, "chrome-linux64", "chrome"),
        path.join(root, d, "chrome-linux", "chrome"),
      ]) {
        try {
          readdirSync(path.dirname(bin));
          return bin;
        } catch {
          // seguir buscando
        }
      }
    }
  } catch {
    // resolución propia de playwright
  }
  return undefined;
}

// Frase con espacio, dígitos y símbolo — ejercita keystrokes "difíciles".
const PHRASE = "Focus ok 2026!";
const results = [];
let failures = 0;

function record(page, input, ok, detail = "") {
  const line = `[${ok ? "PASS" : "FAIL"}] ${page} · ${input}${detail ? " — " + detail : ""}`;
  console.log(line);
  results.push(line);
  if (!ok) failures++;
}

/**
 * Escribe una frase carácter a carácter en el input seleccionado y afirma
 * que el foco permanece en él tras CADA keystroke (el síntoma exacto del
 * repro: una letra y el foco salta a otro elemento).
 *
 * El valor esperado es valorOriginal + lo tecleado (los formularios traen
 * prefill) y la frase se adapta al tipo de input (email/number sanitizan
 * espacios y no-numéricos — no es un bug de foco).
 */
async function typeAssertFocus(page, pageName, selector, phrase = null) {
  const el = await page.$(selector);
  if (!el) {
    record(pageName, selector, true, "input desapareció al re-render (filtro/lista) — skipped");
    return;
  }
  if (!(await el.isVisible()) || (await el.isDisabled())) {
    record(pageName, selector, true, "no visible/disabled — skipped");
    return;
  }
  const type = (await el.getAttribute("type")) ?? "text";
  const inputMode = (await el.getAttribute("inputmode")) ?? "";
  // Frase explícita del caller manda; si no, adaptar al tipo de input.
  if (phrase == null) {
    if (type === "email") phrase = "focus@ok.dev";
    else if (type === "number" || inputMode === "numeric") phrase = "2026";
    else if (type === "password") phrase = "Focus-ok-2026!";
    else phrase = PHRASE;
  }
  await el.click();
  const original = await el.inputValue();
  // Prefill: seleccionar todo y reemplazar — el click pone el caret a mitad
  // del texto y el valor esperado sería dependiente de la posición.
  if (original) await page.keyboard.press("Control+A");
  let typed = "";
  for (const ch of phrase) {
    await page.keyboard.type(ch, { delay: 30 });
    typed += ch;
    const state = await page.evaluate(
      ({ sel, typed }) => {
        const input = document.querySelector(sel);
        const active = document.activeElement;
        return {
          focused: active === input,
          value: input ? input.value : null,
          activeDesc: active
            ? active.tagName +
              (active.id ? `#${active.id}` : "") +
              (active.getAttribute("role")
                ? `[role=${active.getAttribute("role")}]`
                : "")
            : "none",
        };
      },
      { sel: selector, typed },
    );
    if (!state.focused) {
      record(
        pageName,
        selector,
        false,
        `foco perdido tras '${ch}' (activeElement=${state.activeDesc}, value='${state.value}')`,
      );
      return;
    }
    if (state.value !== typed) {
      record(
        pageName,
        selector,
        false,
        `value desincronizado tras '${ch}': '${state.value}' ≠ '${typed}'`,
      );
      return;
    }
  }
  record(pageName, selector, true, `'${phrase}' completa sin perder foco`);
}

/** Barrido de TODOS los inputs de texto/textarea visibles de la página. */
async function sweepPageInputs(page, pageName) {
  const selectors = await page.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("input[type=text], input[type=email], input[type=password], input[type=search], input[type=number], input:not([type]), textarea")) {
      if (el.closest("[role=dialog]")) continue; // los dialogs se barren aparte
      if (el.disabled || el.readOnly) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      el.dataset.focusSweepId = `fs-${out.length}`;
      out.push(`[data-focus-sweep-id="${el.dataset.focusSweepId}"]`);
    }
    return out;
  });
  if (selectors.length === 0) {
    record(pageName, "(page inputs)", true, "sin inputs de texto en la página");
    return;
  }
  for (const sel of selectors) await typeAssertFocus(page, pageName, sel);
  // Los formularios son estado local: recargar descarta lo tecleado.
  await page.reload({ waitUntil: "networkidle" });
}

/** Abre un dialog por texto de botón, barre sus inputs y cierra con Escape. */
async function sweepDialog(page, pageName, buttonText, { exact = false, scope = "main" } = {}) {
  // El topbar comparte labels ("Invite user"): scope al contenido de la página.
  const scopeBtn = page.locator(`${scope} button`, { hasText: buttonText });
  const btn = (await scopeBtn.count())
    ? scopeBtn.first()
    : page.locator("button", { hasText: buttonText }).first();
  if (!(await btn.count())) {
    record(pageName, `dialog "${buttonText}"`, false, "botón no encontrado");
    return;
  }
  await btn.click();
  const dialog = page.locator("[role=dialog]");
  try {
    await dialog.waitFor({ state: "visible", timeout: 4000 });
  } catch {
    record(pageName, `dialog "${buttonText}"`, false, "no abrió dialog");
    return;
  }
  const selectors = await page.evaluate(() => {
    const d = document.querySelector("[role=dialog]");
    const out = [];
    for (const el of d.querySelectorAll("input, textarea")) {
      if (el.disabled || el.readOnly) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      el.dataset.focusSweepId = `fsd-${out.length}`;
      out.push(`[data-focus-sweep-id="${el.dataset.focusSweepId}"]`);
    }
    return out;
  });
  if (selectors.length === 0) {
    record(pageName, `dialog "${buttonText}"`, true, "dialog sin inputs — nada que barrer");
  } else {
    for (const sel of selectors) await typeAssertFocus(page, `${pageName} · dialog "${buttonText}"`, sel);
  }
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden", timeout: 4000 }).catch(() => {});
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromiumExecutable(),
  });

  // ------------------------------------------------------------------
  // 1) Admin Console + auth panel
  // ------------------------------------------------------------------
  if (!COOKIES_FILE) {
    console.error("FAIL: CONSOLE_COOKIES es requerido (export de scripts/e2e_policies_ux.py).");
    process.exit(2);
  }
  const state = JSON.parse(readFileSync(COOKIES_FILE, "utf8"));
  const ctx = await browser.newContext();
  await ctx.addCookies(
    Object.entries(state.cookies).map(([name, value]) => ({
      name,
      value,
      domain: "127.0.0.1",
      path: "/",
    })),
  );
  const page = await ctx.newPage();

  // Auth panel (sin sesión): formularios de login/registro.
  const authCtx = await browser.newContext();
  const authPage = await authCtx.newPage();
  for (const [name, path_] of [
    ["auth /login", "/login"],
    ["auth /register", "/register"],
    ["auth /signup", "/signup"],
  ]) {
    await authPage.goto(CONSOLE_BASE + path_, { waitUntil: "networkidle" });
    if (!authPage.url().includes(path_)) {
      record(name, "(redirect)", true, `redirige a ${authPage.url()} (gated) — skipped`);
      continue;
    }
    await sweepPageInputs(authPage, name);
  }
  await authCtx.close();

  // Páginas autenticadas: inputs de página.
  const consolePages = [
    "overview",
    "organization",
    "departments",
    "users",
    "budgets",
    "usage",
    "models",
    "policies",
    "agents",
    "devices",
    "hermes-download",
    "credentials",
    "audit",
    "branding",
  ];
  for (const p of consolePages) {
    await page.goto(`${CONSOLE_BASE}/${p}`, { waitUntil: "networkidle" });
    await sweepPageInputs(page, `console /${p}`);
  }

  // Diálogos del console (allowlist segura — nunca submit).
  await page.goto(`${CONSOLE_BASE}/departments`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /departments", "Add department");
  await sweepDialog(page, "console /departments", "Rename");

  await page.goto(`${CONSOLE_BASE}/budgets`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /budgets", "Edit budget");
  await sweepDialog(page, "console /budgets", "Edit");

  await page.goto(`${CONSOLE_BASE}/users`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /users", "Invite user");

  await page.goto(`${CONSOLE_BASE}/credentials`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /credentials", "New connection");
  await sweepDialog(page, "console /credentials", "Add secret");

  await page.goto(`${CONSOLE_BASE}/models`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /models", "Approve model");

  await page.goto(`${CONSOLE_BASE}/organization`, { waitUntil: "networkidle" });
  await sweepDialog(page, "console /organization", "New policy");

  // Policies: input inline "+ Add item" (policy-control con allow_custom_items).
  await page.goto(`${CONSOLE_BASE}/policies`, { waitUntil: "networkidle" });
  const addItem = page.locator("button", { hasText: "+ Add item" }).first();
  if (await addItem.count()) {
    await addItem.click();
    await page
      .locator("[aria-label^='Add item']")
      .first()
      .waitFor({ state: "visible", timeout: 4000 })
      .then(async () => {
        await typeAssertFocus(page, "console /policies · add-item inline", "[aria-label^='Add item']");
      })
      .catch(() => record("console /policies", "add-item inline", true, "no apareció input inline"));
  } else {
    record("console /policies", "add-item inline", true, "sin políticas allow_custom visibles");
  }
  await ctx.close();

  // ------------------------------------------------------------------
  // 2) Provider Console
  // ------------------------------------------------------------------
  if (!PROVIDER_PASSWORD) {
    console.error("WARN: PROVIDER_ADMIN_PASSWORD ausente — se salta el Provider Console.");
  } else {
    const pc = await browser.newContext();
    const pp = await pc.newPage();
    // Login (el form en sí es parte del barrido: tecleamos frase completa).
    // La frase del password ES la contraseña real — el barrido de foco
    // deja el form listo para el submit real.
    await pp.goto(`${PROVIDER_BASE}/login`, { waitUntil: "networkidle" });
    await typeAssertFocus(pp, "provider /login", "#username", "provider-admin");
    await typeAssertFocus(pp, "provider /login", "#password", PROVIDER_PASSWORD);
    await pp.locator("button[type=submit]").click();
    await pp.waitForURL(/tenants|change-password/, { timeout: 8000 });
    if (pp.url().includes("change-password")) {
      // Flujo real must_change_password: cambiar y continuar.
      const fields = await pp.evaluate(() =>
        Array.from(document.querySelectorAll("input")).map((i) => i.id).filter(Boolean),
      );
      for (const fid of fields) {
        await typeAssertFocus(pp, "provider /change-password", `#${fid}`, PROVIDER_PASSWORD + "X");
      }
      await pp.locator("button[type=submit]").click();
      await pp.waitForURL(/tenants/, { timeout: 8000 });
    }
    // Provision form (página completa, sin submit).
    await pp.goto(`${PROVIDER_BASE}/tenants/new`, { waitUntil: "networkidle" });
    await sweepPageInputs(pp, "provider /tenants/new");
    // Register provider (dialog, sin submit).
    await pp.goto(`${PROVIDER_BASE}/providers`, { waitUntil: "networkidle" });
    await sweepDialog(pp, "provider /providers", "Register provider");
    // Delete tenant… (ConfirmDialog tipado): texto FALSO + Escape — jamás confirma.
    await pp.goto(`${PROVIDER_BASE}/tenants/arce-demo`, { waitUntil: "networkidle" });
    await sweepDialog(pp, "provider /tenants/arce-demo", "Delete tenant");
    await pc.close();
  }

  await browser.close();

  // ------------------------------------------------------------------
  console.log(`\n==== FOCUS SWEEP: ${results.length - failures}/${results.length} PASS, ${failures} FAIL ====`);
  if (failures) {
    console.log("Inputs que pierden foco (repro t_90e899e0 vivo):");
    for (const r of results.filter((l) => l.startsWith("[FAIL]"))) console.log("  " + r);
    process.exit(1);
  }
  console.log("Ningún input pierde el foco al escribir — repro Miller cerrado.");
}

main().catch((e) => {
  console.error("ERROR:", e);
  process.exit(1);
});

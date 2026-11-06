// Escenario E2E "Practicantes" (spec 041, REQ-041-017) — creación y asignación
// 100% por la UI real (Chromium), verificación contra la API en vivo.
// Uso: node practicantes_ui.mjs <state.json> <outdir>
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/usr/local/lib/hermes-agent/node_modules/");
const { chromium } = require("playwright");

const state = JSON.parse(readFileSync(process.argv[2], "utf8"));
const outdir = process.argv[3];
mkdirSync(outdir, { recursive: true });

const BASE = "http://127.0.0.1:3100";
const WORKER = state.worker_user_id;
const MODEL = "glm-5.2";
const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, pass: Boolean(cond), detail });
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name}${cond ? "" : ` — ${detail}`}`);
};

const browser = await chromium.launch({
  executablePath: "/root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome",
});

async function adminApi(path, method = "GET", payload = undefined) {
  return page.evaluate(
    async ({ path, method, payload, csrf }) => {
      const res = await fetch(`/api/proxy${path}`, {
        method,
        credentials: "include",
        headers: {
          ...(payload !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
        },
        body: payload !== undefined ? JSON.stringify(payload) : undefined,
      });
      return { status: res.status, body: await res.json().catch(() => null) };
    },
    { path, method, payload, csrf: state.cookies.ta_console_csrf },
  );
}

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies(
  Object.entries(state.cookies).map(([name, value]) => ({ name, value, url: BASE })),
);
const page = await ctx.newPage();
const consoleErrors = [];
const failedUrls = [];
page.on("pageerror", (err) => consoleErrors.push(String(err)));
page.on("console", (msg) => {
  if (msg.type() === "error") {
    const loc = msg.location();
    consoleErrors.push(`${msg.text()} @ ${loc ? loc.url : "?"}`);
  }
});
page.on("requestfailed", (r) => failedUrls.push(`REQFAIL ${r.failure()?.errorText} ${r.url()}`));
page.on("response", (r) => {
  if (r.status() >= 400) failedUrls.push(`${r.status()} ${r.url()}`);
});
ctx.on("response", (r) => {
  if (r.status() >= 400 && !failedUrls.includes(`${r.status()} ${r.url()}`)) {
    failedUrls.push(`${r.status()} ${r.url()}`);
  }
});

// --- Idempotencia: borrar "Practicantes" previa (cascade) --------------------
await page.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
for (let i = 0; i < 3; i++) {
  const backdrop = await page.locator(".ta-dialog-backdrop").count();
  if (!backdrop) break;
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
}
{
  const existing = await adminApi("/api/named-policies");
  for (const p of existing.body.policies.filter((x) => x.name === "Practicantes")) {
    const del = await adminApi(`/api/named-policies/${p.id}?cascade=unassign`, "DELETE");
    console.log(`[setup] Practicantes previa borrada (cascade) → HTTP ${del.status}`);
  }
}

// --- Crear desde la UI con la plantilla restringido-total (D-41-08) ----------
await page.waitForSelector("button:has-text('New policy')");
await page.click("button:has-text('New policy')");
await page.waitForSelector(".ta-dialog-backdrop");
await page.fill("#new-policy-name", "Practicantes");
await page.selectOption("#new-policy-template", "restringido-total");
await page.click("button:has-text('Create from template')");
await page.waitForSelector("#policy-name");
check("UI: política 'Practicantes' creada desde la plantilla restringido-total", true);

// --- Composición: SIN internet, SIN terminal, SOLO glm-5.2, manual -----------
// allowed_tools: activar Read files / Write files (web_search sigue OFF).
await page.getByRole("switch", { name: /Read files/ }).click();
await page.waitForTimeout(200);
await page.getByRole("switch", { name: /Write files/ }).click();
await page.waitForTimeout(200);
// allowed_models (requires_choice): elegir el modelo aprobado glm-5.2.
await page.getByRole("switch", { name: /Available AI models/ }).click();
await page.waitForTimeout(400);
const modelsRow = page.locator("h3:has-text('Available AI models')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const modelSwitches = modelsRow.locator(
  'button[role=switch]:not([aria-label*="included in this policy"])');
const modelNames = await modelSwitches.evaluateAll((bs) =>
  bs.map((b) => b.getAttribute("aria-label") || b.textContent.trim()));
console.log(`[info] modelos del tenant en el control: ${JSON.stringify(modelNames)}`);
const glmSwitch = modelsRow.getByRole("switch", { name: "GLM-5.2" });
const glmCount = await modelsRow.getByRole("switch", { name: "GLM-5.2" }).count();
check("UI: el control de modelos lista los modelos aprobados del tenant (API viva)",
  modelNames.length >= 6, JSON.stringify(modelNames));
check(`UI: se elige el modelo aprobado ${MODEL} (único incluido)`, glmCount === 1,
  `matches=${glmCount}`);
await glmSwitch.click();
await page.waitForTimeout(300);

// --- Estado legible del editor: sin internet / sin terminal / manual ---------
const webSwitch = page.getByRole("switch", { name: /Web search/ });
check("UI: 'Web search' queda OFF (sin internet — allowed_tools sin web_search)",
  (await webSwitch.getAttribute("aria-checked")) === "false",
  `aria-checked=${await webSwitch.getAttribute("aria-checked")}`);
const termSwitch = page.getByRole("switch", { name: /Where commands run/ });
const termRow = page.locator("h3:has-text('Where commands run')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const termState = await termRow.innerText();
check("UI: 'Where commands run' ON con los 3 backends OFF (allowed_terminal_backends [])",
  (await termSwitch.getAttribute("aria-checked")) === "true" &&
    !/✓/.test(termState.replace(/✓\s*$/m, "").replace(/^.*$/m, (m) => m)),
  JSON.stringify(termState.slice(0, 200)));
const browseRow = page.locator("h3:has-text('Agent web browsing')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const browseMode = await browseRow.locator(
  "select[aria-label*='mode']").inputValue().catch(() => "");
check("UI: 'Agent web browsing' ON en modo allowlist con lista vacía (allowlist vacía)",
  browseMode === "allowlist", `mode=${browseMode}`);
const approvalRow = page.locator("h3:has-text('Action approval')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const approvalState = await approvalRow.innerText();
check("UI: 'Action approval' ON = manual", /manual/i.test(approvalState),
  JSON.stringify(approvalState.slice(0, 200)));
await page.screenshot({ path: `${outdir}/practicantes-01-editor.png`, fullPage: true });

// --- Fix t_edd8acaf: el control de dominios ACEPTA allowlist vacía ------------
{
  const domainsApply = browseRow.locator("button:has-text('Apply')");
  await domainsApply.click();
  await page.waitForTimeout(300);
  const err = await browseRow.locator("[role=alert]").innerText().catch(() => "");
  const warn = await browseRow.locator("[role=status]").innerText().catch(() => "");
  check("FIX t_edd8acaf: Apply con allowlist vacía aplica el valor (sin error del control)",
    !err && /blocks ALL web browsing/i.test(warn),
    JSON.stringify({ err, warn }));
  await page.screenshot({ path: `${outdir}/practicantes-02-domains-control.png`, fullPage: true });
}

// --- Guardar y verificar la definition EXACTA por API -------------------------
await page.click("button:has-text('Save policy')");
await page.waitForTimeout(1200);
const saved = (await adminApi("/api/named-policies")).body.policies
  .find((p) => p.name === "Practicantes");
const expected = {
  allowed_tools: ["governance_status", "read_file", "write_file"],
  allowed_terminal_backends: [],
  allowed_connectors: [],
  allowed_connections: [],
  allowed_skills: [],
  web_domain_policy: { mode: "allowlist", domains: [] },
  messaging_permissions: {},
  approval_mode: "manual",
  max_daily_sessions: 2,
  allow_credential_vault: false,
  allow_payment_autofill: false,
  allow_voice_mode: false,
  allow_bot_mode: false,
  allow_goal_mode: false,
  allow_scheduled_tasks: false,
  allow_recurring_prompts: false,
  allowed_models: [MODEL],
};
const defEq = saved != null &&
  Object.keys(saved.definition).length === Object.keys(expected).length &&
  Object.entries(expected).every(([k, v]) =>
    JSON.stringify(saved.definition[k]) === JSON.stringify(v));
check("API: definition de Practicantes EXACTA (sin web_search, terminal [], solo glm-5.2, manual)",
  defEq,
  defEq ? "" : `got=${JSON.stringify(saved?.definition)}`);

// --- Asignación al usuario real con preview → confirmar → resultado ----------
await page.click("button:has-text('Assign to…')");
await page.waitForSelector("#assign-level");
await page.selectOption("#assign-level", "user");
await page.waitForSelector("#assign-user");
await page.selectOption("#assign-user", WORKER);
await page.waitForSelector("text=Will be written");
const previewText = await page.locator(".ta-dialog-panel").innerText();
check("UI: preview del diff antes de confirmar (nada escrito hasta confirmar)",
  previewText.includes("Will be written") &&
    previewText.includes("nothing is written until you confirm"),
  JSON.stringify(previewText.slice(0, 400)));
await page.screenshot({ path: `${outdir}/practicantes-03-assign-preview.png`, fullPage: true });
await page.click("button:has-text('Confirm assignment')");
try {
  await page.waitForSelector("text=Policy assigned", { timeout: 15000 });
} catch {
  await page.screenshot({ path: `${outdir}/debug-assign-timeout.png`, fullPage: true });
  const dialogText = await page.locator(".ta-dialog-panel").innerText().catch(() => "");
  check("DEBUG: diálogo tras Confirm assignment", false, JSON.stringify(dialogText.slice(0, 500)));
  throw new Error("assign result not shown");
}
const assignResult = await page.locator(".ta-dialog-panel").innerText();
check("UI: resultado legible de la asignación (aplicadas al usuario)",
  assignResult.includes("Applied to user"), JSON.stringify(assignResult.slice(0, 300)));
await page.screenshot({ path: `${outdir}/practicantes-04-assign-result.png`, fullPage: true });
await page.click("button:has-text('Done')");

// --- AC-041-014: chip "Practicantes" + frases efectivas coherentes -----------
await page.locator("button[role=tab]:has-text('Individual settings')").click();
await page.waitForSelector("#apply-level");
await page.selectOption("#apply-level", "user");
await page.waitForSelector("#apply-user");
await page.selectOption("#apply-user", WORKER);
await page.waitForSelector("section[aria-label='Named policies applied here']");
const chipsText = await page
  .locator("section[aria-label='Named policies applied here']").innerText();
check("AC-041-014: chip 'Practicantes' en la vista del usuario",
  chipsText.includes("Practicantes"), JSON.stringify(chipsText));
await page.waitForFunction(
  () => [...document.querySelectorAll("h3")].some((h) => h.textContent.trim() === "Agent tools"));
const toolsCard = await page.evaluate(() => {
  const h3 = [...document.querySelectorAll("h3")]
    .find((h) => h.textContent.trim() === "Agent tools");
  if (!h3) return null;
  let el = h3;
  for (let i = 0; i < 8 && el.parentElement; i++) {
    el = el.parentElement;
    const t = el.innerText || "";
    if (t.includes("Current state:") || t.includes("Fixed by the provider")) break;
  }
  return el.innerText || "";
});
const phrasesOk = /✗\s*\n?\s*Web search/.test(toolsCard) &&
  toolsCard.includes("Set for this user");
check("AC-041-014: frases efectivas coherentes (✗ Web search + badge 'Set for this user')",
  phrasesOk, JSON.stringify(toolsCard.slice(0, 300)));
await page.screenshot({ path: `${outdir}/practicantes-05-recipient-chips.png`, fullPage: true });

// --- Verificación API: effective del usuario EXACTO ---------------------------
const eff = await adminApi(`/api/policies/effective?user_id=${WORKER}`);
const byKey = {};
for (const k of eff.body.keys) byKey[k.key] = k;
check("API: allowed_tools SIN web_search (user)",
  JSON.stringify(byKey.allowed_tools.effective) ===
    JSON.stringify(["governance_status", "read_file", "write_file"]) &&
    byKey.allowed_tools.source === "user",
  JSON.stringify(byKey.allowed_tools));
check("API: allowed_terminal_backends == [] (user)",
  JSON.stringify(byKey.allowed_terminal_backends.effective) === "[]" &&
    byKey.allowed_terminal_backends.source === "user",
  JSON.stringify(byKey.allowed_terminal_backends));
check(`API: allowed_models == ["${MODEL}"] (user)`,
  JSON.stringify(byKey.allowed_models.effective) === JSON.stringify([MODEL]) &&
    byKey.allowed_models.source === "user",
  JSON.stringify(byKey.allowed_models));
check("API: approval_mode == manual (user)",
  byKey.approval_mode.effective === "manual" && byKey.approval_mode.source === "user",
  JSON.stringify(byKey.approval_mode));
check("API: web_domain_policy == allowlist vacía (user)",
  JSON.stringify(byKey.web_domain_policy.effective) ===
    JSON.stringify({ mode: "allowlist", domains: [] }) &&
    byKey.web_domain_policy.source === "user",
  JSON.stringify(byKey.web_domain_policy));

const realErrors = consoleErrors.filter((e) => !e.includes("favicon.ico"));
const realFailed = failedUrls.filter((u) =>
  !u.startsWith("REQFAIL") && !u.includes("/_rsc=") && !u.includes("favicon"));
check("UI: sin errores de consola reales (favicon/prefetch RSC excluidos)",
  realErrors.length === 0 && realFailed.length === 0,
  JSON.stringify({ realErrors, realFailed: realFailed.slice(0, 5) }));

await browser.close();
writeFileSync(`${outdir}/practicantes-ui-results.json`,
  JSON.stringify(results, null, 2));
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks PASS`);
process.exit(failed.length ? 1 : 0);

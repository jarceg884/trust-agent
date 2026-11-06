// Evidencia E2E spec 041 — Policy Builder de políticas nombradas (UI real).
// Requiere sesiones ya minteadas por scripts/e2e_policies_ux.py (PKCE+TOTP).
// Uso: node scripts/e2e_named_policies_shots.mjs <state.json> <outdir>
//
// Cubre: AC-041-012 (lista+editor+sidebar del contrato, composición D-41-03,
//        requires_choice bloquea el guardado), AC-041-013 (assign con preview
//        + skipped org-only + resultado legible), AC-041-014 (chips por
//        destinatario), D-41-06 (stale + Apply changes), D-41-07 (409 →
//        cascade), AC-041-015 (dept_admin sin Organization en el asignador).
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/usr/local/lib/hermes-agent/node_modules/");
const { chromium } = require("playwright");

const state = JSON.parse(readFileSync(process.argv[2], "utf8"));
const outdir = process.argv[3];
mkdirSync(outdir, { recursive: true });

const BASE = "http://127.0.0.1:3100";
const WORKER = state.worker_user_id;
const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, pass: Boolean(cond), detail });
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name}${cond ? "" : ` — ${detail}`}`);
};

const browser = await chromium.launch({
  executablePath: "/root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome",
});

/** Llamada API con las cookies de la sesión admin (para verificación). */
async function adminApi(path, method = "GET", payload = undefined) {
  const r = await page.evaluate(
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
  return r;
}

const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies(
  Object.entries(state.cookies).map(([name, value]) => ({ name, value, url: BASE })),
);
const page = await ctx.newPage();
const consoleErrors = [];
page.on("pageerror", (err) => consoleErrors.push(String(err)));
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});

// --- Lista: 3 plantillas sembradas con badge (AC-041-012) --------------------
// Idempotencia: si una corrida anterior dejó la policy de prueba, se borra
// (con cascade) antes de empezar.
await page.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
{
  const existing = await adminApi("/api/named-policies");
  for (const p of existing.body.policies.filter((x) => x.name === "E2e Builder Policy")) {
    const del = await adminApi(
      `/api/named-policies/${p.id}?cascade=unassign`, "DELETE");
    console.log(`[setup] política previa borrada (cascade) → HTTP ${del.status}`);
  }
  // La unassign del snapshot desactiva el override de usuario que el flujo
  // de spec 036 dejó (allowed_tools sin web_search) → se re-escribe para que
  // el preview del assign muestre el caso "Replaces:" de forma determinista.
  const put = await adminApi(`/api/policies/user/${WORKER}`, "PUT", {
    key: "allowed_tools",
    value: ["read_file", "write_file", "governance_status"],
  });
  console.log(`[setup] override de usuario restored → HTTP ${put.status}`);
}
for (let i = 0; i < 3; i++) {
  const backdrop = await page.locator(".ta-dialog-backdrop").count();
  if (!backdrop) break;
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
}
await page.waitForSelector("button:has-text('New policy')");
const listApi = await adminApi("/api/named-policies");
const templates = listApi.body.policies.filter((p) => p.source_template);
const templateBadges = await page.locator("span:has-text('Template')").count();
check(
  "AC-041-012: lista muestra las plantillas sembradas con badge Template",
  templates.length === 3 && templateBadges >= 3,
  `api_templates=${templates.length} badges=${templateBadges}`,
);
const builderBlock = listApi.body.builder;
check(
  "AC-041-003: bloque builder del backend (6 grupos, 47 ítems, 3 plantillas)",
  builderBlock.groups.length === 6 &&
    builderBlock.items.length === 47 &&
    builderBlock.templates.length === 3,
  `groups=${builderBlock.groups.length} items=${builderBlock.items.length} templates=${builderBlock.templates.length}`,
);
await page.screenshot({ path: `${outdir}/builder-01-list.png`, fullPage: true });

// --- Nueva política en blanco → editor con el sidebar del contrato -----------
await page.locator("button:has-text('New policy')").click();
await page.waitForSelector(".ta-dialog-backdrop");
await page.fill("#new-policy-name", "E2e Builder Policy");
await page.click("button:has-text('Start editing')");
await page.waitForSelector("#policy-name");
const groupHeaders = await page.$$eval(
  "section[id^='builder-group-'] h2",
  (els) => els.map((e) => e.textContent.trim()),
);
const apiGroups = builderBlock.groups.map((g) => g.name.en);
check(
  "AC-041-012: editor con los 6 grupos del contrato (nada hardcodeado)",
  groupHeaders.length === 6 &&
    apiGroups.every((g) => groupHeaders.includes(g)),
  `dom=${JSON.stringify(groupHeaders)} api=${JSON.stringify(apiGroups)}`,
);
const capabilityRows = await page.locator("section[id^='builder-group-'] h3").count();
check(
  "AC-041-012: las 47 capacidades del contrato en el sidebar",
  capabilityRows === builderBlock.items.length,
  `rows=${capabilityRows} api=${builderBlock.items.length}`,
);
const summary0 = await page.locator("h2:has-text('capabilities active')").textContent()
  .catch(() => "");
check("AC-041-012: resumen '0 capabilities active' al empezar",
  /0 capabilities active/.test(summary0), JSON.stringify(summary0));

// --- Composición D-41-03: primer ítem ON activa hermanos con default_on ------
await page.getByRole("switch", { name: /Web search/ }).click();
await page.waitForTimeout(300);
const summary1 = await page.locator("h2:has-text('capabilities active')").textContent();
check(
  "AC-041-012: activar 'Web search' compone allowed_tools (4 ítems ON visibles)",
  /4 capabilities active/.test(summary1),
  JSON.stringify(summary1),
);
const summaryChips = await page.evaluate(() =>
  [...document.querySelectorAll("li span")].map((s) => s.textContent.trim()),
);
check(
  "AC-041-012: el resumen muestra la key compuesta con sus ítems legibles",
  summaryChips.some((t) => t.includes("Web search, Read files, Write files, View governance status")),
  JSON.stringify(summaryChips.filter((t) => t.includes("Web search"))),
);

// --- Scalar ON → control de spec 036 (sub-toggles docker/local/ssh) -----------
await page.getByRole("switch", { name: /Where commands run/ }).click();
await page.waitForTimeout(300);
const terminalRow = page.locator("h3:has-text('Where commands run')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const backendToggles = await terminalRow.locator("button[role=switch]").count();
check(
  "AC-041-012: capacidad escalar ON muestra su control 036 (docker/local/ssh)",
  backendToggles >= 4, // 1 capability toggle + 3 sub-toggles
  `toggles=${backendToggles}`,
);

// --- Regresión t_edd8acaf: allowlist VACÍA componible desde política en blanco -
// (el control 036 de dominios antes rechazaba {allowlist, []} con un error;
// REQ-041-017 la exige como valor de primera clase.)
await page.getByRole("switch", { name: /Agent web browsing/ }).click();
await page.waitForTimeout(300);
const browseRow = page.locator("h3:has-text('Agent web browsing')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
await browseRow.locator("select[aria-label*='mode']").selectOption("allowlist");
await page.waitForTimeout(200);
const warnBefore = await browseRow.locator("[role=status]").innerText().catch(() => "");
check(
  "t_edd8acaf: allowlist sin dominios muestra advertencia 'blocks ALL web browsing'",
  /blocks ALL web browsing/i.test(warnBefore),
  JSON.stringify(warnBefore),
);
await browseRow.locator("button:has-text('Apply')").click();
await page.waitForTimeout(300);
const browseErr = await browseRow.locator("[role=alert]").innerText().catch(() => "");
check(
  "t_edd8acaf: Apply con allowlist vacía aplica {allowlist, []} (sin error del control)",
  !browseErr,
  JSON.stringify(browseErr),
);
await page.screenshot({ path: `${outdir}/builder-02a-empty-allowlist.png`, fullPage: true });

// --- requires_choice bloquea el guardado (AC-041-012: error inline, no PUT) --
await page.getByRole("switch", { name: /Available AI models/ }).click();
await page.waitForTimeout(300);
const beforeCount = (await adminApi("/api/named-policies")).body.policies.length;
await page.click("button:has-text('Create policy')");
await page.waitForSelector("[role=alert]");
const blockError = await page.locator("[role=alert]").first().innerText();
check(
  "AC-041-012: guardar sin elegir modelo → error inline, NO se crea",
  blockError.includes("Choose a value for: Available AI models") &&
    (await adminApi("/api/named-policies")).body.policies.length === beforeCount,
  JSON.stringify(blockError),
);
await page.screenshot({ path: `${outdir}/builder-02-blocked-save.png`, fullPage: true });

// --- Elección dinámica (models → GET /api/models) y guardado real ------------
const modelsRow = page.locator("h3:has-text('Available AI models')")
  .locator("xpath=ancestor::div[contains(@class,'px-4')][1]");
const modelSwitch = modelsRow.locator(
  'button[role=switch]:not([aria-label*="included in this policy"])',
);
const modelCount = await modelSwitch.count();
check(
  "D-36-05/041: la elección de modelos es dinámica (GET /api/models)",
  modelCount > 0,
  `model toggles=${modelCount}`,
);
await modelSwitch.first().click();
await page.waitForTimeout(200);
// MFA (org_only) también en la policy → preview/assign con skipped_org_only.
await page.getByRole("switch", { name: /Multi-factor authentication/ }).click();
await page.waitForTimeout(200);
await page.click("button:has-text('Create policy')");
try {
  await page.waitForSelector("button:has-text('Save policy')", { timeout: 10000 });
} catch {
  await page.screenshot({ path: `${outdir}/debug-save-timeout.png`, fullPage: true });
  const buttons = await page.$$eval("button", (bs) =>
    bs.filter((b) => b.offsetParent !== null).map((b) => b.textContent.trim()));
  check(
    "DEBUG: estado del editor tras Create policy",
    false,
    `buttons=${JSON.stringify(buttons.slice(0, 15))} errors=${JSON.stringify(consoleErrors)}`,
  );
  throw new Error("save did not complete — see debug-save-timeout.png");
}
const created = (await adminApi("/api/named-policies")).body.policies
  .find((p) => p.name === "E2e Builder Policy");
check(
  "AC-041-012: policy creada con la definition compuesta",
  created &&
    Object.keys(created.definition).length === 5 &&
    "allowed_tools" in created.definition &&
    "allowed_models" in created.definition &&
    "require_mfa" in created.definition &&
    "web_domain_policy" in created.definition,
  created ? JSON.stringify(Object.keys(created.definition)) : "not found",
);
check(
  "t_edd8acaf: definition incluye web_domain_policy {allowlist, []} compuesta desde blanco",
  created &&
    JSON.stringify(created.definition.web_domain_policy) ===
      JSON.stringify({ mode: "allowlist", domains: [] }),
  created ? JSON.stringify(created.definition.web_domain_policy) : "not found",
);
await page.screenshot({ path: `${outdir}/builder-03-editor-saved.png`, fullPage: true });

// --- Assign con preview (AC-041-013): usuario → skipped org-only -------------
await page.click("button:has-text('Assign to…')");
await page.waitForSelector("#assign-level");
await page.selectOption("#assign-level", "user");
await page.waitForSelector("#assign-user");
await page.selectOption("#assign-user", WORKER);
await page.waitForSelector("text=Will be written");
const previewText = await page.locator(".ta-dialog-panel").innerText();
check(
  "AC-041-013: preview del diff (keys a escribir + reemplazos legibles)",
  previewText.includes("Will be written") &&
    previewText.includes("Agent tools") &&
    previewText.includes("Replaces:"),
  JSON.stringify(previewText.slice(0, 400)),
);
check(
  "AC-041-013: preview avisa las org_only omitidas a nivel usuario",
  previewText.includes("Skipped — organization-level only") &&
    previewText.includes("Multi-factor authentication"),
  "",
);
await page.screenshot({ path: `${outdir}/builder-04-assign-preview.png`, fullPage: true });
await page.click("button:has-text('Confirm assignment')");
try {
  await page.waitForSelector("text=Policy assigned", { timeout: 15000 });
} catch {
  await page.screenshot({ path: `${outdir}/debug-assign-timeout.png`, fullPage: true });
  const dialogText = await page.locator(".ta-dialog-panel").innerText()
    .catch(() => "(dialog closed)");
  check(
    "DEBUG: estado del diálogo tras Confirm assignment",
    false,
    `dialog=${JSON.stringify(dialogText.slice(0, 500))} errors=${JSON.stringify(consoleErrors)}`,
  );
  throw new Error("assign result not shown — see debug-assign-timeout.png");
}
const assignResult = await page.locator(".ta-dialog-panel").innerText();
check(
  "AC-041-013: resultado legible (aplicadas + omitidas)",
  assignResult.includes("Applied to user") &&
    assignResult.includes("Skipped — organization-level only"),
  JSON.stringify(assignResult.slice(0, 300)),
);
await page.click("button:has-text('Done')");
// Verificación API: la efectiva del worker refleja lo materializado.
const eff = await adminApi(`/api/policies/effective?user_id=${WORKER}`);
const effTools = eff.body.keys.find((k) => k.key === "allowed_tools");
const effMfa = eff.body.keys.find((k) => k.key === "require_mfa");
check(
  "AC-041-013/005: effective del worker = lo materializado (web_search ON, MFA sigue org)",
  effTools.effective.includes("web_search") && effTools.source === "user" &&
    effMfa.source === "org",
  `tools=${JSON.stringify(effTools.effective)} src=${effTools.source} mfa_src=${effMfa.source}`,
);

// --- Chips por destinatario (AC-041-014) --------------------------------------
await page.locator("button[role=tab]:has-text('Individual settings')").click();
await page.waitForSelector("#apply-level");
await page.selectOption("#apply-level", "user");
await page.waitForSelector("#apply-user");
await page.selectOption("#apply-user", WORKER);
await page.waitForSelector("section[aria-label='Named policies applied here']");
const chipsText = await page
  .locator("section[aria-label='Named policies applied here']")
  .innerText();
check(
  "AC-041-014: chips de la policy nombrada junto a las frases efectivas",
  chipsText.includes("E2e Builder Policy"),
  JSON.stringify(chipsText),
);
await page.screenshot({ path: `${outdir}/builder-05-recipient-chips.png`, fullPage: true });

// --- Edición → stale → Apply changes (D-41-06) --------------------------------
await page.locator("button[role=tab]:has-text('Policies')").click();
await page.waitForSelector("button:has-text('New policy')");
await page.locator("h3:has-text('E2e Builder Policy')")
  .locator("xpath=ancestor::button[1]").click();
await page.waitForSelector("#policy-name");
await page.getByRole("switch", { name: /Private network access/ }).click();
await page.waitForTimeout(200);
await page.click("button:has-text('Save policy')");
await page.waitForSelector("button:has-text('Apply changes')", { timeout: 10000 });
const staleVisible = await page.locator("text=Outdated").first().isVisible();
check(
  "D-41-06: editar marca la asignación Outdated (badge + Apply changes)",
  staleVisible && (await page.locator("button:has-text('Apply changes')").count()) === 1,
  `stale=${staleVisible}`,
);
await page.screenshot({ path: `${outdir}/builder-06-stale.png`, fullPage: true });
await page.click("button:has-text('Apply changes')");
await page.waitForSelector("text=Re-apply results", { timeout: 10000 });
const reapplyText = await page.locator("text=Re-apply results")
  .locator("xpath=ancestor::div[1]").innerText();
check(
  "D-41-06/REQ-041-006: reapply por destinatario con resultado legible",
  reapplyText.includes("✓") && reapplyText.includes("keys re-applied"),
  JSON.stringify(reapplyText.slice(0, 300)),
);

// --- Borrado con asignaciones → 409 → cascade (D-41-07) -----------------------
await page.click("button:has-text('All policies')");
await page.waitForSelector("button:has-text('New policy')");
await page.locator("button[aria-label='Delete policy E2e Builder Policy']").click();
await page.waitForSelector(".ta-dialog-backdrop");
await page.click("button:has-text('Delete policy')");
await page.waitForSelector("text=has active assignments", { timeout: 10000 });
const conflictText = await page.locator(".ta-dialog-panel").innerText();
check(
  "D-41-07: 409 con destinatarios + 'Unapply everywhere and delete'",
  conflictText.includes("has active assignments") &&
    conflictText.includes("Unapply everywhere and delete") &&
    conflictText.includes("User"),
  JSON.stringify(conflictText.slice(0, 300)),
);
await page.screenshot({ path: `${outdir}/builder-07-delete-409.png`, fullPage: true });
await page.click("button:has-text('Unapply everywhere and delete')");
await page.waitForSelector("button:has-text('New policy')", { timeout: 10000 });
await page.waitForTimeout(500);
const afterDelete = (await adminApi("/api/named-policies")).body.policies;
check(
  "D-41-07: cascade desasigna y borra (la policy desaparece)",
  !afterDelete.some((p) => p.name === "E2e Builder Policy"),
  `policies=${afterDelete.map((p) => p.name).join(", ")}`,
);
const effAfter = await adminApi(`/api/policies/effective?user_id=${WORKER}`);
const toolsAfter = effAfter.body.keys.find((k) => k.key === "allowed_tools");
check(
  "D-41-07/AC-041-005: la unassign desactiva las keys del snapshot (hereda de nuevo)",
  toolsAfter.source === "default",
  `tools=${JSON.stringify(toolsAfter.effective)} src=${toolsAfter.source}`,
);

// --- dept_admin: asignador SIN Organization (D-41-09/AC-041-015) --------------
const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx2.addCookies(
  Object.entries(state.dept_admin_cookies).map(([name, value]) => ({ name, value, url: BASE })),
);
const page2 = await ctx2.newPage();
await page2.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
await page2.waitForSelector("button:has-text('New policy')");
check(
  "AC-041-015: dept_admin VE el builder (policies.dept.manage)",
  true,
);
await page2.locator("h3:has-text('Desarrollador')")
  .locator("xpath=ancestor::button[1]").click();
await page2.waitForSelector("#policy-name");
await page2.click("button:has-text('Assign to…')");
await page2.waitForSelector("#assign-level");
const orgOption = await page2.locator("#assign-level option[value=org]").count();
check(
  "D-41-09: dept_admin NO puede asignar a la organización (sin opción org)",
  orgOption === 0,
  `org_options=${orgOption}`,
);
await page2.screenshot({ path: `${outdir}/builder-08-dept-admin.png`, fullPage: true });

// --- Navegación por teclado del builder (AC-041-015) --------------------------
await page.focus("button[role=tab]:has-text('Policies')");
await page.keyboard.press("ArrowRight");
const activeTab = await page.evaluate(() =>
  document.querySelector("button[role=tab][aria-selected=true]")?.textContent.trim());
check(
  "AC-041-015: flechas navegan las pestañas por teclado",
  activeTab === "Individual settings",
  `active=${activeTab}`,
);

await browser.close();
writeFileSync(`${outdir}/named-policies-e2e-results.json`,
  JSON.stringify(results, null, 2));
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks PASS`);
process.exit(failed.length ? 1 : 0);

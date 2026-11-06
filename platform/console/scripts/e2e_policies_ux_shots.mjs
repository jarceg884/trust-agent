// Evidencia E2E spec 036 — capturas + verificaciones DOM del panel legible.
// Requiere sesiones ya minteadas por scripts/e2e_policies_ux.py (PKCE+TOTP real).
// Uso: node scripts/e2e_policies_ux_shots.mjs <state.json> <outdir>
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
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name}${cond ? "" : " — " + detail}`);
};

const browser = await chromium.launch({
  // Chromium ya presente en el cache de Playwright del host (1217).
  executablePath:
    "/root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies(
  Object.entries(state.cookies).map(([name, value]) => ({
    name, value, url: BASE,
  })),
);
const page = await ctx.newPage();

// --- Vista por defecto (org): builder spec 041 → pestaña Individual settings --
// (D-41-10: el builder es ahora la vista por defecto; el panel legible de
// la spec 036 vive en la pestaña "Individual settings", intacto.)
await page.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
for (let i = 0; i < 3; i++) {
  const backdrop = await page.locator(".ta-dialog-backdrop").count();
  if (!backdrop) break;
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
  if (await page.locator(".ta-dialog-backdrop").count()) {
    const skip = page.locator("button", { hasText: "Skip for now" });
    if (await skip.count()) await skip.click();
    await page.waitForTimeout(400);
  }
}
// El builder (spec 041) es la vista por defecto (D-41-10).
const newPolicyBtn = await page.locator("button", { hasText: "New policy" }).count();
check("D-41-10: el Policy Builder es la vista por defecto", newPolicyBtn === 1,
  `count=${newPolicyBtn}`);
await page.screenshot({ path: `${outdir}/policies-ux-builder-default.png`, fullPage: true });
await page.locator("button", { hasText: "Individual settings" }).click();
await page.waitForSelector("#apply-level");
const h2s = await page.$$eval("h2", (els) => els.map((e) => e.textContent.trim()));
const CATEGORIES = [
  "Security & session", "Internet & network", "Agent & automation",
  "Terminal & execution", "Extensions & memory", "Data & privacy",
  "AI models", "Apps & messaging", "Users & access",
];
check("AC-036-002: 9 categorías visibles", CATEGORIES.every((c) => h2s.includes(c)),
  `h2=${JSON.stringify(h2s)}`);
check("AC-036-002: sección Provider protections", h2s.includes("Provider protections"));
// 0 keys crudas en texto visible — slugs derivados de la API en vivo.
const keys = await page.evaluate(async () => {
  const r = await fetch("/api/proxy/api/policies/matrix", { credentials: "include" });
  const j = await r.json();
  return j.catalog.map((e) => e.key);
});
const bodyText = await page.evaluate(() => document.body.innerText);
const leaked = keys.filter((k) => bodyText.includes(k));
check("AC-036-002: 0 keys crudas en el DOM legible", leaked.length === 0,
  `leaked=${JSON.stringify(leaked)}`);
const techBtn = await page.locator("button", { hasText: "Technical view" }).count();
check("AC-036-002: org_admin VE la pestaña Technical view", techBtn === 1);
await page.screenshot({ path: `${outdir}/policies-ux-default-panel.png`, fullPage: true });

// --- Vista técnica (spec 033) conmutable ------------------------------------
await page.locator("button", { hasText: "Technical view" }).click();
await page.waitForSelector("table");
const ths = await page.$$eval("table th", (els) => els.map((e) => e.textContent.trim()));
check("AC-036-002: Vista técnica = tabla maestra spec 033",
  ths.includes("Key") && ths.includes("Provider default") && ths.includes("Origin"),
  `th=${JSON.stringify(ths)}`);
await page.locator("button", { hasText: "Individual settings" }).click();
await page.waitForSelector("#apply-level");

// Busca la card completa desde su h3: sube hasta incluir el cuerpo
// ("Current state:" o "Fixed by the provider").
async function cardFromH3(page, h3Text) {
  return page.evaluate((txt) => {
    const h3 = [...document.querySelectorAll("h3")].find(
      (h) => h.textContent.trim() === txt);
    if (!h3) return null;
    let el = h3;
    for (let i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || "";
      if (t.includes("Current state:") || t.includes("Fixed by the provider")) break;
    }
    return el.innerText;
  }, h3Text);
}

// --- Vista por destinatario: usuario worker (AC-036-005) --------------------
await page.selectOption("#apply-level", "user");
await page.waitForSelector("#apply-user");
await page.selectOption("#apply-user", WORKER);
await page.waitForFunction(
  () => [...document.querySelectorAll("h3")].some((h) => h.textContent.trim() === "Agent tools"),
);
const mfaCard = await cardFromH3(page, "Multi-factor authentication (MFA)");
check("AC-036-005: 'Must sign in with MFA' + 'Inherited from organization'",
  mfaCard.includes("Must sign in with MFA") && mfaCard.includes("Inherited from organization"),
  JSON.stringify(mfaCard));
const toolsCard = await cardFromH3(page, "Agent tools");
check("AC-036-005: '✗ Web search' (sub-toggle por ítem) + badge 'Set for this user'",
  /✗\s*\n?\s*Web search/.test(toolsCard) && toolsCard.includes("Set for this user"),
  JSON.stringify(toolsCard.slice(0, 300)));
await page.screenshot({ path: `${outdir}/policies-ux-user-view.png`, fullPage: true });

// --- 422 inline real (AC-036-004): relajar max_daily_sessions a 20 ----------
await page.selectOption("#apply-level", "org");
await page.waitForFunction(() =>
  [...document.querySelectorAll("h3")].some((h) => h.textContent.trim() === "Max daily sessions"));
// org ya fijó 5 (PUT real del flujo). A nivel usuario, intentar 20 → 422.
await page.selectOption("#apply-level", "user");
await page.waitForSelector("#apply-user");
await page.selectOption("#apply-user", WORKER);
await page.waitForFunction(() =>
  [...document.querySelectorAll("h3")].some((h) => h.textContent.trim() === "Max daily sessions"));
const maxCard = page.locator("h3", { hasText: "Max daily sessions" })
  .locator("xpath=ancestor::div[contains(@class,'rounded-xl')][1]");
await maxCard.locator("input[type=number]").fill("20");
await maxCard.getByRole("button", { name: "Apply" }).click();
await maxCard.locator("[role=alert]").waitFor();
const alertText = await maxCard.locator("[role=alert]").innerText();
check("AC-036-004: 422 real inline (REQ-POL-007) + explicación en palabras",
  alertText.includes("would relax a superior policy") &&
    alertText.includes("can only tighten"),
  JSON.stringify(alertText));
const inputVal = await maxCard.locator("input[type=number]").inputValue();
check("AC-036-004: el control vuelve al valor vigente (5)", inputVal === "5",
  `input=${inputVal}`);
await maxCard.scrollIntoViewIfNeeded();
await page.screenshot({ path: `${outdir}/policies-ux-422-inline.png` });

// --- require_mfa org_only: solo Organization (AC-036-003) --------------------
const mfaBadges = await page.evaluate(() => {
  const h3 = [...document.querySelectorAll("h3")].find(
    (h) => h.textContent.trim() === "Multi-factor authentication (MFA)");
  const card = h3.closest("div").parentElement;
  return { orgWide: card.innerText.includes("Organization-wide"),
    note: card.innerText.includes("Defined at organization level") };
});
check("AC-036-003: require_mfa org_only → badge 'Organization-wide' + nota de nivel org",
  mfaBadges.orgWide && mfaBadges.note, JSON.stringify(mfaBadges));

// --- Invariables: candado sin control (AC-036-003 / D-36-09) -----------------
const locked = await page.evaluate(() => {
  const sec = [...document.querySelectorAll("section")].find((s) =>
    s.getAttribute("aria-label") === "Provider protections");
  const cards = [...sec.querySelectorAll("h3")].map((h) => {
    let el = h;
    for (let i = 0; i < 8 && el.parentElement; i++) {
      el = el.parentElement;
      if ((el.innerText || "").includes("Fixed by the provider")) break;
    }
    const t = el.innerText || "";
    return {
      name: h.textContent.trim(),
      badge: t.includes("Provider protection"),
      noSwitch: el.querySelectorAll('[role="switch"]').length === 0,
      noSelect: el.querySelectorAll("select").length === 0,
      noInput: el.querySelectorAll("input").length === 0,
      fixed: t.includes("Fixed by the provider"),
    };
  });
  return cards;
});
check("AC-036-003: 3 invariables con candado, badge y SIN control",
  locked.length === 3 && locked.every((c) =>
    c.badge && c.noSwitch && c.noSelect && c.noInput && c.fixed),
  JSON.stringify(locked));

// --- dept_admin: SIN conmutador Technical view (AC-036-002) ------------------
const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx2.addCookies(
  Object.entries(state.dept_admin_cookies).map(([name, value]) => ({
    name, value, url: BASE,
  })),
);
const page2 = await ctx2.newPage();
await page2.goto(`${BASE}/policies`, { waitUntil: "networkidle" });
for (let i = 0; i < 3; i++) {
  const backdrop = await page2.locator(".ta-dialog-backdrop").count();
  if (!backdrop) break;
  await page2.keyboard.press("Escape");
  await page2.waitForTimeout(400);
}
await page2.locator("button", { hasText: "Individual settings" }).click();
await page2.waitForSelector("#apply-level");
const techBtn2 = await page2.locator("button", { hasText: "Technical view" }).count();
check("AC-036-002: dept_admin NO ve la pestaña Technical view", techBtn2 === 0,
  `count=${techBtn2}`);
const h2s2 = await page2.$$eval("h2", (els) => els.map((e) => e.textContent.trim()));
check("AC-036-002: dept_admin ve el panel legible por defecto",
  h2s2.includes("Security & session") && h2s2.includes("Users & access"),
  JSON.stringify(h2s2.slice(0, 6)));
await page2.screenshot({ path: `${outdir}/policies-ux-dept-admin.png`, fullPage: true });

// --- Buscador es+en (AC-036-002) ---------------------------------------------
await page.selectOption("#apply-level", "org");
await page.waitForTimeout(600);
await page.fill("#policy-search", "mfa");
await page.waitForTimeout(600);
const afterSearch = await page.evaluate(() => ({
  cards: [...document.querySelectorAll("h3")].filter((h) => h.offsetParent !== null)
    .map((h) => h.textContent.trim()),
  sections: [...document.querySelectorAll("h2")].filter((h) => h.offsetParent !== null)
    .map((h) => h.textContent.trim()),
}));
check("AC-036-002: buscador 'mfa' filtra a la card de MFA",
  afterSearch.cards.length === 1 &&
    afterSearch.cards[0] === "Multi-factor authentication (MFA)" &&
    afterSearch.sections.includes("Security & session"),
  JSON.stringify(afterSearch));
// Búsqueda en español también cubre (contrato es+en).
await page.fill("#policy-search", "multifactor");
await page.waitForTimeout(600);
const esSearch = await page.evaluate(() =>
  [...document.querySelectorAll("h3")].filter((h) => h.offsetParent !== null)
    .map((h) => h.textContent.trim()));
check("AC-036-002: buscador encuentra por texto en español ('multifactor')",
  esSearch.includes("Multi-factor authentication (MFA)"),
  JSON.stringify(esSearch));
await page.fill("#policy-search", "");
await page.waitForTimeout(400);

// --- Navegación por teclado (AC-036-008) --------------------------------------
await page.focus("#apply-level");
let focusables = [];
for (let i = 0; i < 8; i++) {
  await page.keyboard.press("Tab");
  const tag = await page.evaluate(() =>
    `${document.activeElement.tagName.toLowerCase()}#${document.activeElement.id || ""}`);
  focusables.push(tag);
}
const uniqueFocus = new Set(focusables);
const allInteractive = focusables.every((t) =>
  /^(select|input|button)/.test(t));
check("AC-036-008: Tab recorre controles reales (select/input/button, sin trampas de foco)",
  allInteractive && uniqueFocus.size >= 3,
  JSON.stringify([...uniqueFocus]));

await browser.close();
writeFileSync(`${outdir}/policies-ux-e2e-results.json`,
  JSON.stringify(results, null, 2));
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} checks PASS`);
process.exit(failed.length ? 1 : 0);

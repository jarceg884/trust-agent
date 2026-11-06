// E2E EN VIVO — spec 042 REQ-PE-007 / AC-007-2 (GAP-42-C, t_059bf0db):
// la página Usage se auto-refresca cada 15 s (patrón presencia Devices,
// spec 027) — el admin ve los tokens subir SIN F5 ni clic, con badge
// "· live", pausado con la pestaña oculta y sin resetear filtros ni
// paginación del drill-down. Nada simulado: el consumo nuevo se genera
// con requests REALES por el gateway LiteLLM (:4000) con la key virtual
// del worker enrolado (perfil trust-live-prac), que el spend-worker
// persiste en spend_ledger (ciclo 2 s) — las cifras de la UI salen de
// /api/usage sobre ese ledger.
//
// Uso: node e2e_usage_live.mjs <state.json> <outdir>
//   state.json: cookies de sesión admin del console (/tmp/builder-state.json)
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
const require = createRequire("/usr/local/lib/hermes-agent/node_modules/");
const { chromium } = require("playwright");

const state = JSON.parse(readFileSync(process.argv[2], "utf8"));
const outdir = process.argv[3];
mkdirSync(outdir, { recursive: true });

const BASE = "http://127.0.0.1:3100";
const GATEWAY = { host: "127.0.0.1", port: 4000 };
const WORKER_EMAIL = "worker@policies-ux.test";
const results = [];
const check = (name, cond, detail = "") => {
  results.push({ name, pass: Boolean(cond), detail });
  console.log(`[${cond ? "PASS" : "FAIL"}] ${name}${cond ? "" : ` — ${detail}`}`);
};

// Key virtual del perfil enrolado — se lee del config y NUNCA se imprime
// (mismo patrón que evidence_041_practicantes_live.py).
const workerKey = (() => {
  for (const line of readFileSync(
    "/root/.hermes/profiles/trust-live-prac/config.yaml",
    "utf8",
  ).split(/\r?\n/)) {
    if (line.trim().startsWith("api_key:")) {
      return line.slice(line.indexOf("api_key:") + "api_key:".length).trim();
    }
  }
  throw new Error("api_key not found in trust-live-prac config.yaml");
})();

/** Request REAL al gateway (upstream MaaS) — genera consumo en el ledger. */
function gatewayChat(maxTokens = 8) {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { ...GATEWAY, path: "/v1/chat/completions", method: "POST" },
      (res) => {
        let raw = "";
        res.on("data", (c) => (raw += c));
        res.on("end", () =>
          resolve({ status: res.statusCode, body: JSON.parse(raw) }),
        );
      },
    );
    req.on("error", reject);
    req.setHeader("Content-Type", "application/json");
    req.setHeader("Authorization", `Bearer ${workerKey}`);
    req.end(
      JSON.stringify({
        model: "glm-5.2",
        max_tokens: maxTokens,
        messages: [{ role: "user", content: "Responde solo: OK" }],
      }),
    );
  });
}

const browser = await chromium.launch({
  executablePath: "/root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addCookies(
  Object.entries(state.cookies).map(([name, value]) => ({ name, value, url: BASE })),
);
const page = await ctx.newPage();

// Observabilidad: errores de consola, requests fallidos y cadencia del poll.
const consoleErrors = [];
const failedUrls = [];
const usageFetches = []; // timestamps de GET /api/usage (cadencia + pausa)
const drillFetches = []; // URLs de GET /api/usage/requests (offset intacto)
page.on("pageerror", (err) => consoleErrors.push(String(err)));
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("requestfailed", (r) => failedUrls.push(`REQFAIL ${r.url()}`));
page.on("response", (r) => {
  if (r.status() >= 400) failedUrls.push(`${r.status()} ${r.url()}`);
});
page.on("request", (r) => {
  const url = r.url();
  if (url.includes("/api/proxy/api/usage?")) usageFetches.push(Date.now());
  if (url.includes("/api/proxy/api/usage/requests")) drillFetches.push(url);
});

async function apiGet(path) {
  return page.evaluate(async (p) => {
    const res = await fetch(`/api/proxy${p}`, { credentials: "include" });
    return { status: res.status, body: await res.json() };
  }, path);
}

const badge = page.locator('div[title^="Auto-refreshes"]');
async function badgeCount() {
  const txt = (await badge.textContent()).trim();
  const m = txt.match(/([\d,]+)\s*requests?/);
  return m ? Number(m[1].replace(/,/g, "")) : null;
}

// --- Carga de la página (sin F5 posterior en todo el test) ------------------
await page.goto(`${BASE}/usage`, { waitUntil: "networkidle" });
// Convención e2e_041: descartar diálogos previos del shell (p. ej. el
// onboarding "Organization structure") que quedan montados sobre la página.
for (let i = 0; i < 3; i++) {
  if ((await page.locator(".ta-dialog-backdrop").count()) === 0) break;
  await page.keyboard.press("Escape");
  await page.waitForTimeout(400);
}
await badge.waitFor({ timeout: 10_000 });
await page.locator(`tr:has-text("${WORKER_EMAIL}")`).waitFor({ timeout: 10_000 });

// --- C1: badge "· live" visible ---------------------------------------------
{
  const txt = (await badge.textContent()).trim();
  check(
    "C1 badge live visible en el header de Usage (· live, lenguaje Devices)",
    txt.includes("· live"),
    txt,
  );
}

// --- C2: paridad UI == API (cifras del ledger, cero sintéticos) -------------
const beforeApi = await apiGet("/api/usage?period=30d");
const beforeBadge = await badgeCount();
check(
  "C2 paridad UI==API: el badge muestra el totals.requests de /api/usage",
  beforeBadge === beforeApi.body.totals.requests,
  `badge=${beforeBadge} api=${beforeApi.body.totals.requests}`,
);
const beforeRowCells = await page
  .locator(`tr:has-text("${WORKER_EMAIL}") td`)
  .allTextContents();

// --- C3 (AC-007-2): request nuevo visible sin F5 ni clic, ≤ 15 s ------------
// 5 requests reales por el gateway → spend_ledger (ciclo worker 2 s).
const firedAt = Date.now();
for (let i = 0; i < 5; i++) {
  const r = await gatewayChat();
  if (r.status !== 200) check(`gateway request ${i + 1}`, false, `HTTP ${r.status}`);
}
// Momento en que el PRIMER request nuevo es visible en el ledger (API).
let tLedger = null;
for (let i = 0; i < 30; i++) {
  const r = await apiGet("/api/usage/requests?period=30d&limit=1");
  if (r.body.total > beforeBadge) {
    tLedger = Date.now();
    break;
  }
  await page.waitForTimeout(1_000);
}
check(
  "C3a los 5 requests reales llegaron al ledger (spend-worker, ciclo 2 s)",
  tLedger !== null,
  `total esperado > ${beforeBadge}`,
);
// Momento en que la UI lo muestra SIN F5 ni clic (solo el ciclo de 15 s).
let tUi = null;
for (let i = 0; i < 40; i++) {
  const n = await badgeCount();
  if (n !== null && n > beforeBadge) {
    tUi = Date.now();
    break;
  }
  await page.waitForTimeout(500);
}
const pickupMs = tUi !== null && tLedger !== null ? tUi - tLedger : null;
check(
  "C3b AC-007-2: la página muestra el request nuevo sin F5 ni clic, ≤ 15 s",
  pickupMs !== null && pickupMs <= 15_000 + 1_500,
  `ledger→UI = ${pickupMs !== null ? (pickupMs / 1000).toFixed(1) : "nunca"} s (tolerancia +1.5 s de granularidad del poll)`,
);
// El badge actualiza en cada borde de 15 s: esperar a que los 5 lleguen a la UI.
for (let i = 0; i < 40 && (await badgeCount()) !== beforeBadge + 5; i++) {
  await page.waitForTimeout(1_000);
}
await page.waitForTimeout(1_000);
const afterBadge = await badgeCount();
const afterRowCells = await page
  .locator(`tr:has-text("${WORKER_EMAIL}") td`)
  .allTextContents();
// Columnas del drill: User, Department, Requests, Tokens in, Tokens out, …
const num = (cells, i) => Number(cells[i].replace(/,/g, "").trim());
check(
  "C3c la fila del usuario subió en vivo (requests y tokens del ledger)",
  afterBadge === beforeBadge + 5 &&
    num(afterRowCells, 2) > num(beforeRowCells, 2) &&
    num(afterRowCells, 3) > num(beforeRowCells, 3),
  `requests ${num(beforeRowCells, 2)}→${num(afterRowCells, 2)}, tokens_in ${num(beforeRowCells, 3)}→${num(afterRowCells, 3)}`,
);
await page.screenshot({ path: `${outdir}/usage-live-updated.png`, fullPage: true });

// --- Cadencia del auto-refresco (ritmo 15 s del patrón Devices) -------------
{
  const visible = usageFetches.filter((t) => t > firedAt && t < firedAt + 35_000);
  const gaps = visible.slice(1).map((t, i) => t - visible[i]);
  check(
    "C3d cadencia del poll ≈ 15 s mientras la pestaña es visible",
    gaps.length >= 1 && gaps.every((d) => d >= 12_000 && d <= 19_000),
    `deltas(s)=${gaps.map((d) => (d / 1000).toFixed(1)).join(",")}`,
  );
}

// --- C4: pausa con la pestaña oculta + refresco al volver --------------------
// Nota honesta: Chromium headless NO oculta pestañas en segundo plano
// (verificado: 2ª tab, headed+xvfb, --headless=new y CDP
// Target.activateTarget — document.hidden siempre false). Se conduce el
// contrato estándar de la web: override de document.hidden/visibilityState
// + evento visibilitychange REAL — el listener y el guard de la página son
// los de producción; solo la señal de oclusión de la pestaña es inyectada.
async function setTabHidden(hidden) {
  await page.evaluate((h) => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => h,
    });
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => (h ? "hidden" : "visible"),
    });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);
}
let hiddenFetches = 0;
const hiddenListener = (r) => {
  const u = r.url();
  if (u.includes("/api/proxy/api/usage") || u.includes("/api/devices/presence")) {
    hiddenFetches++;
  }
};
await setTabHidden(true);
check(
  "C4a la pestaña pasa a oculta (document.hidden)",
  (await page.evaluate(() => document.hidden)) === true,
);
page.on("request", hiddenListener);
await page.waitForTimeout(20_000); // cruza ≥1 borde del intervalo de 15 s
check(
  "C4b el refresco se PAUSA con la pestaña oculta (0 fetches en 20 s)",
  hiddenFetches === 0,
  `fetches durante oculta=${hiddenFetches}`,
);
await setTabHidden(false); // visibilitychange → tick() inmediato
await page.waitForTimeout(3_000);
page.removeListener("request", hiddenListener);
check(
  "C4c al volver a ser visible se refresca al momento (≤ 3 s)",
  hiddenFetches >= 1,
  `fetch tras volver visible=${hiddenFetches - 0}`,
);

// --- C5: el refresco no resetea filtros ni paginación del drill-down --------
// Click vía JS: el click de Playwright abre el diálogo y su propio backdrop
// intercepta el retry de actionability (timeout falso).
await page
  .locator(`tr:has-text("${WORKER_EMAIL}")`)
  .locator("button")
  .evaluate((el) => el.click());
const drillDialog = page.locator(".ta-dialog-backdrop").filter({ hasText: "Requests —" });
await drillDialog.waitFor({ timeout: 10_000 });
await drillDialog.locator("table").waitFor({ timeout: 10_000 });
const totalRequests = (await apiGet("/api/usage/requests?period=30d&limit=1")).body.total;
check(
  "C5a hay >25 requests reales → el drill-down tiene 2 páginas",
  totalRequests > 25,
  `total=${totalRequests}`,
);
const drillNav = drillDialog.locator("nav[aria-label='Pagination']");
// Click vía JS: el nav queda bajo el fold del diálogo (fuera del viewport).
await drillNav
  .locator("button", { hasText: "Next" })
  .evaluate((el) => el.click());
await drillNav.locator("p").waitFor();
const page2Text = (await drillNav.locator("p").textContent()).trim();
check(
  "C5b drill-down en página 2 (offset 25)",
  page2Text.startsWith("26–"),
  page2Text,
);
// Un ciclo completo de refresco silencioso con el diálogo abierto: la
// paginación y los filtros deben quedar intactos (sin skeleton visible).
drillFetches.length = 0;
await page.waitForTimeout(17_000);
const page2TextAfter = (await drillNav.locator("p").textContent()).trim();
const dialogStillOpen = (await drillDialog.count()) === 1;
const drillOffsets = drillFetches.map((u) => Number(new URL(u).searchParams.get("offset")));
check(
  "C5c tras el ciclo de 15 s: diálogo abierto, MISMA página, offset intacto",
  dialogStillOpen && page2TextAfter === page2Text,
  `antes='${page2Text}' después='${page2TextAfter}' dialog=${dialogStillOpen}`,
);
check(
  "C5d el re-fetch en silencio del drill-down conservó offset=25 (sin reset)",
  drillOffsets.length >= 1 && drillOffsets.every((o) => o === 25),
  `offsets=${JSON.stringify(drillOffsets)}`,
);
const periodValue = await page.locator("select").first().inputValue();
check(
  "C5e filtros de la página intactos (período sigue 'Last 30 days')",
  periodValue === "30d",
  `period=${periodValue}`,
);
await page.screenshot({ path: `${outdir}/usage-drill-page2-live.png`, fullPage: true });

// --- C6: cero errores de consola / requests fallidos -------------------------
// Ruido pre-existing excluido (ajeno a esta feature, presente en todas las
// páginas): favicon.ico 404 y prefetches RSC del sidebar abortados (?_rsc=).
const relevantErrors = consoleErrors.filter(
  (e) => !e.includes("favicon") && !e.includes("404"),
);
const relevantFailures = failedUrls.filter(
  (u) => !u.includes("?_rsc=") && !u.includes("favicon"),
);
check(
  "C6 sin errores JS ni requests de API fallidos durante todo el flujo",
  relevantErrors.length === 0 && relevantFailures.length === 0,
  `js=${JSON.stringify(relevantErrors)} api=${JSON.stringify(relevantFailures)} (excluidos como ruido pre-existing: favicon 404 + prefetch RSC abortados)`,
);

await browser.close();

const passed = results.filter((r) => r.pass).length;
writeFileSync(
  `${outdir}/usage-live-results.json`,
  JSON.stringify(
    {
      scenario: "spec 042 REQ-PE-007/AC-007-2 — Usage en vivo (GAP-42-C)",
      pickup_ledger_to_ui_seconds: pickupMs !== null ? pickupMs / 1000 : null,
      before_requests: beforeBadge,
      after_requests: afterBadge,
      results,
    },
    null,
    2,
  ),
);
console.log(`\n${passed}/${results.length} checks PASS`);
process.exit(passed === results.length ? 0 : 1);

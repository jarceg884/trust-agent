/**
 * Regression test (browser click-through) — AC-26-008 / t_d139d01b.
 *
 * Bug fixed: the "Activate with this budget" panel in
 * src/app/(provider)/tenants/[slug]/tenant-detail.tsx sent
 * `body: JSON.stringify({...})` to the `api()` helper, which stringifies
 * again — the control plane received a JSON-encoded STRING and answered
 * 422 "Input should be a valid dictionary or object to extract fields
 * from". The panel was never exercised end-to-end from a browser, which
 * is why it slipped; this script drives the REAL panel against the REAL
 * stack so it cannot regress silently again.
 *
 * Journey (nothing mocked):
 *   1. Fixture via public API: POST /api/auth/signup-org → tenant
 *      pending_activation (spec 026 self-signup).
 *   2. Browser: sign in to the Provider Console as provider-admin.
 *   3. Open /tenants/<slug>, fill the initial budget, click
 *      "Activate with this budget" and confirm in the dialog.
 *   4. REGRESSION ASSERTION: the intercepted POST body must parse to a
 *      plain OBJECT with initial_budget_usd — with the double-stringify
 *      bug it parses to a *string* and the API answers 422.
 *   5. End-to-end: HTTP 200, tenant status active, budget visible in UI.
 *
 * Usage (stack must be up: api :8000 + provider-console :3200):
 *   PROVIDER_ADMIN_PASSWORD='...' node e2e/activate-panel.mjs
 * Env:
 *   PC_BASE_URL            (default http://127.0.0.1:3200)
 *   API_BASE_URL           (default http://127.0.0.1:8000)
 *   PROVIDER_ADMIN_USER    (default provider-admin)
 *   PROVIDER_ADMIN_PASSWORD (required)
 *   PLAYWRIGHT_NODE_PATH   (node_modules with `playwright`, fallback:
 *                          /usr/local/lib/hermes-agent/node_modules)
 *   PLAYWRIGHT_CHROMIUM_PATH (fallback: newest ~/.cache/ms-playwright/
 *                          chromium-<ver>/chrome-linux64/chrome)
 */

import { createRequire } from "node:module";
import { readdirSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const BASE = process.env.PC_BASE_URL ?? "http://127.0.0.1:3200";
const API_BASE = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
const USER = process.env.PROVIDER_ADMIN_USER ?? "provider-admin";
const PASSWORD = process.env.PROVIDER_ADMIN_PASSWORD;
if (!PASSWORD) {
  console.error("FAIL: PROVIDER_ADMIN_PASSWORD is required.");
  process.exit(2);
}

/** Resolve `playwright` from NODE_PATH-style candidates (no local dep). */
function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_NODE_PATH,
    "/usr/local/lib/hermes-agent/node_modules",
    ...(process.env.NODE_PATH ?? "").split(path.delimiter),
  ].filter(Boolean);
  for (const dir of candidates) {
    try {
      const req = createRequire(path.join(dir, "noop.js"));
      return req("playwright");
    } catch {
      // try next candidate
    }
  }
  throw new Error(
    "playwright not found — set PLAYWRIGHT_NODE_PATH to a node_modules " +
      "dir containing the `playwright` package.",
  );
}

/** Newest cached chromium binary (version-agnostic fallback). */
function chromiumExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  const root = path.join(homedir(), ".cache", "ms-playwright");
  try {
    const dirs = readdirSync(root)
      .filter((d) => /^chromium-\d+$/.test(d))
      .sort();
    for (const d of dirs.reverse()) {
      const bin = path.join(root, d, "chrome-linux64", "chrome");
      const binAlt = path.join(root, d, "chrome-linux", "chrome");
      try {
        readdirSync(path.dirname(bin));
        return bin;
      } catch {
        try {
          readdirSync(path.dirname(binAlt));
          return binAlt;
        } catch {
          // keep looking
        }
      }
    }
  } catch {
    // fall through to playwright's own resolution
  }
  return undefined;
}

let passes = 0;
const failures = [];
function check(name, ok, detail = "") {
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? `  · ${detail}` : ""}`);
  if (ok) passes += 1;
  else failures.push(name);
}

async function main() {
  const slug = `e2e-act-${Math.floor(Date.now() / 1000)}`;
  const orgName = "E2E Activate Panel Corp";
  const budgetUsd = 60;

  // --- 1. Fixture: pending tenant via the public self-signup endpoint ---
  const signupRes = await fetch(`${API_BASE}/api/auth/signup-org`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      organization_name: orgName,
      slug,
      admin_email: `founder@${slug}.test`,
      admin_password: "E2eFounder2026!Pass",
      admin_display_name: "E2E Founder",
    }),
  });
  const signupBody = await signupRes.json().catch(() => ({}));
  check(
    "fixture: POST /api/auth/signup-org → 201 pending_activation",
    signupRes.status === 201 && signupBody.status === "pending_activation",
    `HTTP ${signupRes.status} ${JSON.stringify(signupBody).slice(0, 140)}`,
  );
  if (signupRes.status !== 201) process.exit(1);

  const { chromium } = loadPlaywright();
  const browser = await chromium.launch({
    headless: true,
    executablePath: chromiumExecutable(),
  });
  const page = await browser.newPage();

  // Capture the activate request the panel fires (the regression probe).
  const activateCalls = [];
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      req.url().endsWith(`/api/proxy/api/admin/tenants/${slug}/activate`)
    ) {
      activateCalls.push({ postData: req.postData() });
    }
  });

  try {
    // --- 2. Sign in through the real login form ---
    await page.goto(`${BASE}/login`);
    await page.fill("#username", USER);
    await page.fill("#password", PASSWORD);
    await Promise.all([
      page.waitForURL("**/tenants", { timeout: 20_000 }),
      page.click("button[type=submit]"),
    ]);
    check("login: provider-admin lands on /tenants", true, page.url());

    // --- 3. Open the pending tenant detail + the Activate panel ---
    await page.goto(`${BASE}/tenants/${slug}`);
    await page.waitForSelector("text=Activate this organization", {
      timeout: 20_000,
    });
    check("detail: 'Activate this organization' panel is visible", true);

    await page.fill("#initial-budget", String(budgetUsd));
    const responsePromise = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        r.url().endsWith(`/api/admin/tenants/${slug}/activate`),
      { timeout: 30_000 },
    );

    await page.getByRole("button", { name: "Activate with this budget" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ timeout: 10_000 });
    // Confirm inside the dialog (exact label "Activate").
    await dialog
      .getByRole("button", { name: "Activate", exact: true })
      .click();

    const res = await responsePromise;
    const resBody = await res.json().catch(() => ({}));

    // --- 4. REGRESSION: body must be a JSON object, not a string ---
    const call = activateCalls[0];
    check(
      "regression: panel fired exactly one activate POST",
      activateCalls.length === 1,
      `calls=${activateCalls.length}`,
    );
    let parsed = null;
    let parseOk = false;
    if (call?.postData != null) {
      try {
        parsed = JSON.parse(call.postData);
        parseOk = true;
      } catch {
        parseOk = false;
      }
    }
    check(
      "regression: request body is a plain JSON OBJECT (no double stringify)",
      parseOk &&
        typeof parsed === "object" &&
        parsed !== null &&
        !Array.isArray(parsed),
      `postData=${call?.postData ?? "none"}`,
    );
    check(
      "regression: body carries initial_budget_usd as a number",
      parseOk && typeof parsed === "object" && parsed !== null
        ? typeof parsed.initial_budget_usd === "number" &&
          parsed.initial_budget_usd === budgetUsd
        : false,
      `parsed=${JSON.stringify(parsed)}`,
    );

    // --- 5. End-to-end: 200 + tenant active + budget in the UI ---
    check(
      "e2e: POST .../activate → 200",
      res.status() === 200,
      `HTTP ${res.status()} ${JSON.stringify(resBody).slice(0, 160)}`,
    );
    check(
      "e2e: response says tenant active with admins notified",
      resBody?.tenant?.status === "active" &&
        Number(resBody?.notified_org_admins ?? 0) >= 1,
      `status=${resBody?.tenant?.status} notified=${resBody?.notified_org_admins}`,
    );
    // The activate response does not carry org_budget_cents (the UI falls
    // back to the local amount) — read it back through the BFF like the
    // provider list does, so the assertion is DB-backed.
    const listRes = await page.request.get(
      `${BASE}/api/proxy/api/admin/tenants`,
    );
    const listBody = await listRes.json().catch(() => ({}));
    const entry = (listBody?.tenants ?? []).find((t) => t?.slug === slug);
    check(
      "e2e: org budget persisted (read back via BFF)",
      entry?.status === "active" && entry?.org_budget_cents === budgetUsd * 100,
      `status=${entry?.status} org_budget_cents=${entry?.org_budget_cents}`,
    );
    const toast = page.locator('[role="status"]', {
      hasText: "is now active",
    });
    await toast.first().waitFor({ timeout: 15_000 });
    check("e2e: success toast shown in the UI", true);
    await page.waitForSelector("h1 >> text=Active", { timeout: 15_000 });
    check("e2e: header badge switched to Active", true);
  } catch (err) {
    check("journey completed without exceptions", false, String(err).split("\n")[0]);
  } finally {
    await browser.close();
  }

  console.log(`\n${"=".repeat(60)}\nAC-26-008 regression: ${passes} PASS, ${failures.length} FAIL`);
  if (failures.length) {
    console.log("FALLOS:", failures);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("SCRIPT ERROR:", err);
  process.exit(1);
});

/**
 * Regression test (browser click-through) — t_4f4b0eb1, repro Miller.
 *
 * "ese montón de clientes — ¿hay cómo eliminarlos? Yo no he creado
 *  ninguno y no veo botón para eliminar."
 *
 * Journey (nothing mocked — stack real: api :8000 + provider-console :3200):
 *   1. Fixture via public API: POST /api/auth/signup-org → tenant
 *      pending_activation (spec 026 self-signup).
 *   2. Browser: sign in to the Provider Console as provider-admin.
 *   3. Open /tenants/<slug> → the "Delete tenant…" button EXISTS.
 *   4. Click it → ConfirmDialog: type a WRONG slug → the confirm button
 *      must stay DISABLED (fail-closed UI, mirrors the API check).
 *   5. Type the EXACT slug → button enables → click "Delete permanently".
 *   6. REGRESSION ASSERTIONS:
 *      - the intercepted DELETE body is a plain OBJECT {confirm: slug},
 *      - HTTP 200 with deleted:true / realm_deleted:true,
 *      - the UI redirects to /tenants and the tenant is GONE from the list.
 *
 * The delete IS the cleanup (anti-garbage convention: factory tenants
 * never survive their own test).
 *
 * Usage (stack must be up):
 *   PROVIDER_ADMIN_PASSWORD='...' node e2e/delete-panel.mjs
 * Env: same as activate-panel.mjs (PC_BASE_URL, API_BASE_URL,
 *      PROVIDER_ADMIN_USER, PROVIDER_ADMIN_PASSWORD, PLAYWRIGHT_*).
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
      for (const bin of [
        path.join(root, d, "chrome-linux64", "chrome"),
        path.join(root, d, "chrome-linux", "chrome"),
      ]) {
        try {
          readdirSync(path.dirname(bin));
          return bin;
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
  const slug = `e2e-del-${Math.floor(Date.now() / 1000)}`;
  const orgName = "E2E Delete Panel Corp";

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

  // Capture the DELETE the panel fires (the fail-closed regression probe).
  const deleteCalls = [];
  page.on("request", (req) => {
    if (
      req.method() === "DELETE" &&
      req.url().endsWith(`/api/proxy/api/admin/tenants/${slug}`)
    ) {
      deleteCalls.push({ postData: req.postData() });
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

    // --- 3. The Delete button exists on the tenant detail (repro Miller) ---
    await page.goto(`${BASE}/tenants/${slug}`);
    const deleteBtn = page.getByRole("button", { name: "Delete tenant…" });
    await deleteBtn.waitFor({ timeout: 20_000 });
    check("detail: 'Delete tenant…' button is visible", true);

    // --- 4. Fail-closed UI: wrong slug keeps the confirm button disabled ---
    await deleteBtn.click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor({ timeout: 10_000 });
    check("dialog: 'Delete this tenant permanently?' opens", true);

    const confirmBtn = dialog.getByRole("button", {
      name: "Delete permanently",
    });
    check(
      "fail-closed: confirm disabled before typing",
      confirmBtn.isDisabled(),
    );
    await page.fill("#confirm-typed", `${slug}-typo`);
    check(
      "fail-closed: confirm stays disabled with a WRONG slug",
      confirmBtn.isDisabled(),
      `typed=${slug}-typo`,
    );

    // --- 5. Exact slug enables the button; click deletes for real ---
    const responsePromise = page.waitForResponse(
      (r) =>
        r.request().method() === "DELETE" &&
        r.url().endsWith(`/api/admin/tenants/${slug}`),
      { timeout: 30_000 },
    );
    await page.fill("#confirm-typed", slug);
    check(
      "fail-closed: confirm ENABLES with the exact slug",
      !(await confirmBtn.isDisabled()),
    );
    await confirmBtn.click();

    const res = await responsePromise;
    const resBody = await res.json().catch(() => ({}));

    // --- 6. Regression assertions ---
    const call = deleteCalls[0];
    check(
      "regression: panel fired exactly one DELETE",
      deleteCalls.length === 1,
      `calls=${deleteCalls.length}`,
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
      "regression: DELETE body is a plain JSON OBJECT with confirm=slug",
      parseOk &&
        typeof parsed === "object" &&
        parsed !== null &&
        !Array.isArray(parsed) &&
        parsed.confirm === slug,
      `postData=${call?.postData ?? "none"}`,
    );
    check(
      "e2e: DELETE /api/admin/tenants/{slug} → 200 deleted",
      res.status() === 200 && resBody?.deleted === true,
      `HTTP ${res.status()} ${JSON.stringify(resBody).slice(0, 160)}`,
    );
    check(
      "e2e: Keycloak realm removed with the tenant",
      resBody?.realm_deleted === true,
      `realm_deleted=${resBody?.realm_deleted}`,
    );

    // --- 7. UI redirects to /tenants and the tenant is GONE from the list ---
    await page.waitForURL("**/tenants", { timeout: 20_000 });
    check("ui: redirected back to /tenants", true, page.url());
    const listRes = await page.request.get(
      `${BASE}/api/proxy/api/admin/tenants`,
    );
    const listBody = await listRes.json().catch(() => ({}));
    const entry = (listBody?.tenants ?? []).find((t) => t?.slug === slug);
    check(
      "e2e: tenant absent from the provider list (read back via BFF)",
      !entry,
      `entry=${JSON.stringify(entry ?? null)}`,
    );
  } catch (err) {
    check("journey completed without exceptions", false, String(err).split("\n")[0]);
  } finally {
    // Best-effort: if the journey failed midway the tenant may survive —
    // deprovision it through the BFF so this test never leaks (anti-garbage
    // convention, t_4f4b0eb1).
    try {
      const listRes = await page.request.get(
        `${BASE}/api/proxy/api/admin/tenants`,
      );
      const listBody = await listRes.json().catch(() => ({}));
      if ((listBody?.tenants ?? []).some((t) => t?.slug === slug)) {
        const del = await page.request.del(
          `${BASE}/api/proxy/api/admin/tenants/${slug}`,
          { data: { confirm: slug } },
        );
        check(
          "cleanup: leftover e2e tenant deprovisioned",
          del.ok(),
          `HTTP ${del.status()}`,
        );
      }
    } catch {
      // browser context may be gone; nothing to clean
    }
    await browser.close();
  }

  console.log(`\n${"=".repeat(60)}\nt_4f4b0eb1 delete panel: ${passes} PASS, ${failures.length} FAIL`);
  if (failures.length) {
    console.log("FALLOS:", failures);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error("SCRIPT ERROR:", err);
  process.exit(1);
});

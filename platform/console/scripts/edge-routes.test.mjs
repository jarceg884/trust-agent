// Public edge: allowlist + client-address preload (node --test, no extra deps).
// Run: npm run test:edge
import assert from "node:assert/strict";
import http from "node:http";
import { createRequire } from "node:module";
import { describe, it } from "node:test";

import {
  clientAddress,
  isDeviceApiRoute,
  isDownloadRoute,
  isLlmRoute,
} from "../src/lib/edge-routes.ts";

const require = createRequire(import.meta.url);
const preload = require("../edge-client-address.cjs");

const DEVICE = "2f5916dd-040e-4d94-8490-919de8faa26c";

describe("download routes", () => {
  it("forwards a tenant's public download surface", () => {
    for (const path of [
      "/download/e2e-lab-224146",
      "/download/e2e-lab-224146/installer/windows",
      "/download/e2e-lab-224146/certificate",
      "/download/e2e-lab-224146/desktop-config",
      "/download/e2e-lab-224146/cli",
      "/download/e2e-lab-224146/bundle",
    ]) {
      assert.equal(isDownloadRoute("GET", path), true, path);
    }
    assert.equal(isDownloadRoute("HEAD", "/download/e2e-lab-224146/installer/windows"), true);
  });

  it("refuses other methods, invalid slugs and dot segments", () => {
    assert.equal(isDownloadRoute("POST", "/download/e2e-lab-224146"), false);
    assert.equal(isDownloadRoute("GET", "/download/E2E-lab"), false);
    assert.equal(isDownloadRoute("GET", "/download/ab"), false);
    assert.equal(isDownloadRoute("GET", "/download"), false);
    assert.equal(isDownloadRoute("GET", "/download/e2e-lab-224146/../../api/provider"), false);
    assert.equal(isDownloadRoute("GET", "/download/e2e-lab-224146/%2e%2e/x"), false);
    assert.equal(isDownloadRoute("GET", "/download/e2e-lab-224146//cli"), false);
  });
});

describe("device API routes", () => {
  it("forwards exactly what a governed desktop calls", () => {
    for (const [method, path] of [
      ["POST", "/api/devices/enroll"],
      ["POST", "/api/devices/enroll/exchange"],
      ["POST", "/api/devices/token/refresh"],
      ["POST", `/api/devices/${DEVICE}/attestation`],
      ["GET", "/api/me/governance-bundle"],
      ["POST", "/api/me/device-heartbeat"],
      ["GET", "/api/me/update-check"],
      ["GET", `/api/me/agent-profiles/${DEVICE}/package`],
    ]) {
      assert.equal(isDeviceApiRoute(method, path), true, `${method} ${path}`);
    }
  });

  it("keeps device management and everything else off the edge", () => {
    for (const [method, path] of [
      ["GET", "/api/devices"],
      ["GET", "/api/devices/me"],
      ["GET", "/api/devices/presence"],
      ["POST", "/api/devices/preauthorize"],
      ["GET", `/api/devices/${DEVICE}`],
      ["POST", `/api/devices/${DEVICE}/approve`],
      ["POST", `/api/devices/${DEVICE}/revoke`],
      ["POST", `/api/devices/${DEVICE}/token`],
      ["DELETE", `/api/devices/${DEVICE}`],
      ["POST", "/api/devices/not-a-uuid/attestation"],
      ["GET", "/api/devices/enroll/exchange"],
      ["GET", "/api/me"],
      ["GET", "/api/me/sessions"],
      ["POST", "/api/me/governance-bundle"],
      ["GET", "/api/me/agent-profiles"],
      ["GET", "/api/me/agent-profiles/not-a-uuid/package"],
      ["POST", `/api/me/agent-profiles/${DEVICE}/package`],
      ["GET", `/api/me/agent-profiles/${DEVICE}/package/../../governance-bundle`],
      ["GET", "/api/marketplace/profiles"],
      ["GET", `/api/marketplace/profiles/${DEVICE}/package`],
      ["POST", "/api/marketplace/profiles/import"],
    ]) {
      assert.equal(isDeviceApiRoute(method, path), false, `${method} ${path}`);
    }
  });
});

describe("model gateway routes (/llm)", () => {
  it("forwards OpenAI-compatible inference with or without /v1", () => {
    for (const [method, subpath] of [
      ["POST", "chat/completions"],
      ["POST", "v1/chat/completions"],
      ["POST", "completions"],
      ["POST", "v1/embeddings"],
      ["GET", "models"],
      ["GET", "v1/models"],
      ["GET", "v1/models/glm-5.3"],
      ["GET", "models/deepseek/deepseek-v4.1-flash-1017"],
    ]) {
      assert.equal(isLlmRoute(method, subpath), true, `${method} ${subpath}`);
    }
  });

  it("never exposes LiteLLM's admin API or UI", () => {
    for (const [method, subpath] of [
      ["GET", ""],
      ["GET", "ui"],
      ["GET", "health"],
      ["GET", "key/info"],
      ["POST", "key/generate"],
      ["GET", "spend/logs"],
      ["GET", "model/info"],
      ["GET", "v1/model/info"],
      ["POST", "model/new"],
      ["POST", "v1/models"],
      ["GET", "chat/completions"],
      ["GET", "v1/models/../../key/info"],
      ["GET", "v1/models/./x"],
    ]) {
      assert.equal(isLlmRoute(method, subpath), false, `${method} ${subpath}`);
    }
  });
});

describe("client address", () => {
  it("accepts one valid IP and nothing else", () => {
    assert.equal(clientAddress("203.0.113.7"), "203.0.113.7");
    assert.equal(clientAddress("::ffff:203.0.113.7"), "203.0.113.7");
    assert.equal(clientAddress("2001:db8::1"), "2001:db8::1");
    assert.equal(clientAddress("999.1.1.1"), null);
    assert.equal(clientAddress("evil.example"), null);
    assert.equal(clientAddress(""), null);
    assert.equal(clientAddress(null), null);
  });
});

describe("edge-client-address preload", () => {
  const none = new Set();

  it("ignores a forged X-Forwarded-For: the TCP peer wins", () => {
    assert.equal(preload.resolveClientAddress("203.0.113.9", "1.2.3.4", none), "203.0.113.9");
    assert.equal(preload.resolveClientAddress("::ffff:127.0.0.1", undefined, none), "127.0.0.1");
  });

  it("honours trusted proxies: right-most untrusted hop", () => {
    const trusted = preload.parseTrustedProxies("10.0.0.5, ::ffff:10.0.0.6, junk");
    assert.deepEqual([...trusted].sort(), ["10.0.0.5", "10.0.0.6"]);
    assert.equal(
      preload.resolveClientAddress("10.0.0.5", "1.2.3.4, 198.51.100.7", trusted),
      "198.51.100.7",
    );
    assert.equal(
      preload.resolveClientAddress("10.0.0.5", "198.51.100.7, 10.0.0.6", trusted),
      "198.51.100.7",
    );
    assert.equal(preload.resolveClientAddress("10.0.0.5", "garbage", trusted), "10.0.0.5");
  });

  it("rewrites the header on a real server before any handler sees it", async () => {
    preload.install(new Set());
    assert.equal(globalThis[preload.EDGE_MARKER], true);
    const server = http.createServer((req, res) => res.end(String(req.headers["x-forwarded-for"])));
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    try {
      const { port } = server.address();
      const seen = await new Promise((resolve, reject) => {
        http
          .get(
            { host: "127.0.0.1", port, path: "/", headers: { "X-Forwarded-For": "6.6.6.6" } },
            (res) => {
              let body = "";
              res.on("data", (chunk) => (body += chunk));
              res.on("end", () => resolve(body));
            },
          )
          .on("error", reject);
      });
      assert.equal(seen, "127.0.0.1");
    } finally {
      server.close();
    }
  });
});

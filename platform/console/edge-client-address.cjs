/**
 * Client address at the console's public edge (preloaded: NODE_OPTIONS=--require=…).
 *
 * Next.js records the client as `x-forwarded-for ??= socket.remoteAddress`
 * (next/dist/server/base-server.js): a value sent by the client survives, so an address
 * read from that header could be forged — e.g. to dodge the control plane's per-IP
 * throttles on activation and downloads, or to poison its audit trail. The console IS
 * the edge, so before Next.js sees a request this preload replaces the header with the
 * TCP peer. A reverse proxy placed in front later is listed in TRUST_EDGE_TRUSTED_PROXIES;
 * its X-Forwarded-For is then honoured, right-most untrusted hop first.
 *
 * The edge relay (src/lib/edge-proxy.ts) forwards a client address ONLY when this
 * preload is active (EDGE_MARKER): without it the API sees the relay's own loopback.
 */
"use strict";
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS preload (NODE_OPTIONS=--require), loaded before any ESM/TS tooling */

const http = require("node:http");
const net = require("node:net");

const EDGE_MARKER = Symbol.for("trust-agent.edge.client-address");

function normalize(address) {
  const value = String(address || "").trim();
  const mapped = /^::ffff:(\d{1,3}(?:\.\d{1,3}){3})$/i.exec(value);
  return mapped ? mapped[1] : value;
}

function parseTrustedProxies(raw) {
  return new Set(
    String(raw || "")
      .split(",")
      .map(normalize)
      .filter((address) => net.isIP(address) !== 0),
  );
}

/** The client's address given the TCP peer, the inbound X-Forwarded-For and trusted proxies. */
function resolveClientAddress(peer, forwardedFor, trustedProxies) {
  const socketPeer = normalize(peer);
  if (!trustedProxies.has(socketPeer)) return socketPeer;
  const header = Array.isArray(forwardedFor) ? forwardedFor.join(",") : forwardedFor || "";
  const hops = header
    .split(",")
    .map(normalize)
    .filter((address) => net.isIP(address) !== 0);
  for (let index = hops.length - 1; index >= 0; index -= 1) {
    if (!trustedProxies.has(hops[index])) return hops[index];
  }
  return socketPeer;
}

function install(trustedProxies = parseTrustedProxies(process.env.TRUST_EDGE_TRUSTED_PROXIES)) {
  if (globalThis[EDGE_MARKER]) return;
  const createServer = http.createServer;
  http.createServer = function trustAgentEdgeServer(...args) {
    const server = createServer.apply(this, args);
    server.prependListener("request", (request) => {
      const address = resolveClientAddress(
        request.socket && request.socket.remoteAddress,
        request.headers["x-forwarded-for"],
        trustedProxies,
      );
      if (net.isIP(address) !== 0) request.headers["x-forwarded-for"] = address;
      else delete request.headers["x-forwarded-for"];
    });
    return server;
  };
  globalThis[EDGE_MARKER] = true;
}

module.exports = { EDGE_MARKER, install, normalize, parseTrustedProxies, resolveClientAddress };

if (require.main !== module) install();

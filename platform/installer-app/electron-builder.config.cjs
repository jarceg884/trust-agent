// electron-builder config — Trust Agent Setup (spec 040).
//
// Per-tenant values are injected at BUILD time by scripts/build_desktop_installers.py
// through env vars (D-040-02): TA_PRODUCT_NAME, TA_SLUG, TA_PLUGIN_VERSION.
//
// NSIS on Linux: electron-builder v27 runs the built installer once under
// WINE to extract the uninstaller. Host requirement: wine64 + wine32:i386
// (Ubuntu: `dpkg --add-architecture i386 && apt install wine32 wine64`) —
// without the 32-bit loader wine fails with "failed to load
// syswow64\ntdll.dll" (evidence: docs/evidencia/desktop-installers/).
// macOS builds (.dmg) run on a macOS runner — see
// .github/workflows/desktop-installers-macos.yml (D-040-04).
"use strict";

const product = process.env.TA_PRODUCT_NAME || "Trust Agent Setup";
const slug = process.env.TA_SLUG || "org";
const version = process.env.TA_PLUGIN_VERSION || "0.0.0";

/** @type {import("app-builder-lib").Configuration} */
module.exports = {
  appId: "com.trustagent.setup",
  productName: product,
  executableName: "TrustAgentSetup",
  icon: "build/icon",
  artifactName: `trust-agent-setup-${slug}-v\${version}-\${os}-\${arch}.\${ext}`,
  extraMetadata: { version },
  directories: { output: "dist", buildResources: "build" },
  files: ["main.js", "preload.js", "renderer/**"],
  extraResources: [{ from: "resources", to: "resources" }],
  asar: true,
  win: { target: ["nsis"] },
  nsis: {
    oneClick: true,
    perMachine: false,
    deleteAppDataOnUninstall: false,
  },
  linux: { target: ["AppImage"], category: "Utility" },
};

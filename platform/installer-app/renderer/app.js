// Renderer — wizard state machine (welcome → install → sign-in → done).
"use strict";

const $ = (id) => document.getElementById(id);
const screens = ["welcome", "install", "signin", "done"];

function show(name) {
  for (const s of screens) $("screen-" + s).classList.toggle("hidden", s !== name);
}

function err(id, message) {
  const el = $(id);
  el.textContent = message;
  el.classList.remove("hidden");
}

async function main() {
  const cfg = await window.ta.getConfig();
  const b = cfg.branding || {};

  // Tenant branding (baked at build time, D-040-02).
  document.title = (b.product_name || "Trust Agent") + " Setup";
  $("product").textContent = b.product_name || "Trust Agent";
  $("tagline").textContent = b.tagline || "";
  $("kicker").textContent = cfg.binding.slug + " · organization edition";
  $("slug").textContent = cfg.binding.slug;
  $("pversion").textContent = "v" + (cfg.binding.plugin_version || "0");
  $("home").textContent = cfg.home;
  $("done-org").textContent = cfg.binding.slug;
  if (b.accent_color) document.documentElement.style.setProperty("--accent", b.accent_color);
  if (b.logo_data_uri) {
    $("logo").innerHTML = '<img src="' + b.logo_data_uri + '" alt="logo">';
  } else {
    $("logo").innerHTML = '<span class="mark">' +
      (b.product_name || "T").slice(0, 1).toUpperCase() + "</span>";
  }

  window.ta.onInstallLog((line) => {
    const log = $("install-log");
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  });
  window.ta.onSigninLog((line) => {
    const log = $("signin-log");
    log.textContent += line + "\n";
    log.scrollTop = log.scrollHeight;
  });

  $("btn-install").addEventListener("click", async () => {
    $("install-error").classList.add("hidden");
    $("btn-retry").classList.add("hidden");
    $("install-log").textContent = "";
    show("install");
    const res = await window.ta.install();
    if (res.ok) {
      show("signin");
    } else {
      err("install-error", res.error);
      $("btn-retry").classList.remove("hidden");
    }
  });

  $("btn-retry").addEventListener("click", () => $("btn-install").click());

  $("btn-signin").addEventListener("click", async () => {
    $("signin-error").classList.add("hidden");
    const res = await window.ta.signin();
    if (res.ok) show("done");
    else err("signin-error", res.error);
  });

  $("btn-skip-signin").addEventListener("click", () => show("done"));

  $("btn-launch").addEventListener("click", async () => {
    const res = await window.ta.launch();
    if (!res.ok) $("launch-hint").textContent = res.hint || "";
  });

  $("btn-finish").addEventListener("click", () => window.close());
}

main();

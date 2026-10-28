#!/usr/bin/env node
/**
 * Verificación de iconos del contrato legible (spec 036, AC-036-007).
 *
 * Comprueba que los 47 iconos de keys + 9 de categorías declarados en
 * `policy-catalog-readable.json` existen en la versión INSTALADA de
 * lucide-react. El render tiene fallback al icono de categoría (D-36-06),
 * pero este script convierte un icono roto en hallazgo explícito.
 *
 * Uso: node scripts/check_policy_icons.mjs [ruta-del-json]
 * Exit 0 = todos existen; exit 1 = faltan (se listan).
 */
import { readFile } from "node:fs/promises";

const contractPath =
  process.argv[2] ?? "../../app/governance/policy_catalog_readable.json";

const contract = JSON.parse(await readFile(new URL(contractPath, import.meta.url), "utf8"));

// lucide-react expone todos los iconos como named exports de su build ESM.
const lucide = await import("lucide-react");

const names = new Set();
for (const c of contract.categories) names.add(c.icon);
for (const block of Object.values(contract.keys)) names.add(block.icon);

const missing = [...names].filter((n) => !(n in lucide));

console.log(
  `policy-catalog-readable.json: ${contract.categories.length} categorías, ` +
    `${Object.keys(contract.keys).length} keys, ${names.size} iconos únicos.`,
);
if (missing.length) {
  console.error(`FALTAN en lucide-react (${missing.length}): ${missing.join(", ")}`);
  console.error(
    "El render caerá al icono de la categoría (fallback D-36-06) — " +
      "corrige el contrato o el mapa policy-icons.tsx.",
  );
  process.exit(1);
}
console.log(`OK: los ${names.size} iconos existen en lucide-react.`);

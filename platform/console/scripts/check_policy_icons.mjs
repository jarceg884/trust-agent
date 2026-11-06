#!/usr/bin/env node
/**
 * Verificación de iconos de los contratos de políticas (spec 036 AC-036-007
 * + spec 041 AC-041-015).
 *
 * Comprueba que TODOS los iconos declarados en:
 * - `policy_catalog_readable.json` (9 categorías + 47 keys, spec 036), y
 * - `named_policy_builder.json` (6 grupos + 47 capacidades, spec 041),
 * existen en la versión INSTALADA de lucide-react Y en el mapa
 * `policy-icons.tsx` (el render cae al icono del grupo/categoría como
 * fallback, pero este script convierte un icono roto en hallazgo
 * explícito de CI).
 *
 * Uso: node scripts/check_policy_icons.mjs
 * Exit 0 = todos existen; exit 1 = faltan (se listan).
 */
import { readFile } from "node:fs/promises";

const lucide = await import("lucide-react");

// El mapa del design system: los nombres deben estar importados aquí.
const iconsTsx = await readFile(
  new URL("../src/app/(console)/policies/readable/policy-icons.tsx", import.meta.url),
  "utf8",
);

const contracts = [
  {
    path: "../../app/governance/policy_catalog_readable.json",
    label: "policy-catalog-readable.json (spec 036)",
    collect(c) {
      const names = new Set();
      for (const cat of c.categories) names.add(cat.icon);
      for (const block of Object.values(c.keys)) names.add(block.icon);
      return names;
    },
  },
  {
    path: "../../app/governance/named_policy_builder.json",
    label: "named-policy-builder.json (spec 041)",
    collect(c) {
      const names = new Set();
      for (const g of c.groups) names.add(g.icon);
      for (const i of c.items) names.add(i.icon);
      return names;
    },
  },
];

let totalMissing = 0;
for (const contract of contracts) {
  const json = JSON.parse(
    await readFile(new URL(contract.path, import.meta.url), "utf8"),
  );
  const names = contract.collect(json);
  const missingLucide = [...names].filter((n) => !(n in lucide));
  const missingMap = [...names].filter(
    (n) => !new RegExp(`\\b${n}\\b`).test(iconsTsx),
  );
  console.log(`${contract.label}: ${names.size} iconos únicos.`);
  if (missingLucide.length) {
    console.error(`  FALTAN en lucide-react (${missingLucide.length}): ${missingLucide.join(", ")}`);
  }
  if (missingMap.length) {
    console.error(`  FALTAN en policy-icons.tsx (${missingMap.length}): ${missingMap.join(", ")}`);
  }
  if (missingLucide.length || missingMap.length) {
    console.error(
      "  El render caerá al icono del grupo/categoría (fallback) — " +
        "corrige el contrato o el mapa policy-icons.tsx.",
    );
    totalMissing += missingLucide.length + missingMap.length;
  } else {
    console.log(`  OK: los ${names.size} iconos existen en lucide-react y en el mapa.`);
  }
}

if (totalMissing) process.exit(1);

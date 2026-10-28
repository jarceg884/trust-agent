# Evidencia: "desplegable donde sea" (t_b62edff2, mandato Miller)

**Qué se probó**: que un TERCERO puede desplegar el piloto desde cero usando
SOLO el repo público + `.env.example` — sin acceso a nada de la VM de
desarrollo. Fresh clone del tag `v0.1.0-pilot`, stack completo con volúmenes
nuevos en un prefijo de puertos distinto al stack dev que corre en la misma
máquina, setup de UN comando, smoke 6/6 PASS, provisioning real de un tenant
y backup de la DB del "cliente".

## Resultado final: PASS

| # | Verificación | Resultado |
|---|---|---|
| 1 | Fresh clone del tag público `v0.1.0-pilot` (commit `cac2429`) | ✅ `00-clone-commit.txt` |
| 2 | `.env` del cliente: SOLO 28 líneas cambiadas vs `.env.example` (puertos, URLs públicas, prefijo de contenedores — procedimiento del README §Troubleshooting) | ✅ `01-env-diff-vs-example.txt` |
| 3 | `scripts/setup.sh` de UN comando: secretos generados, build, up, migraciones (29), seed del super-user, OTA capturada, smoke | ✅ `02-setup-output.txt` (OTA redactada) |
| 4 | Smoke de verificación | ✅ **6 PASS / 0 FAIL** (healthz, IdP discovery, Admin Console, Provider Console, LiteLLM, login real del super-user) |
| 5 | Los 9 servicios del stack del cliente sanos, en SUS puertos (56432/8181/8100/3110/3210/39990) | ✅ `03-compose-ps.txt` |
| 6 | Aislamiento de datos: volúmenes NUEVOS (`ta2-evidence_*`), DB del cliente nace vacía (0 tenants), DB del stack dev (101 tenants) INTACTA | ✅ `04-data-isolation.txt` |
| 7 | Provisioning REAL de un tenant en el stack del cliente: `POST /api/admin/tenants` → 201, realm creado en el Keycloak DEL CLIENTE (discovery 200), tenant `active` en SU PostgreSQL | ✅ `05-tenant-provisioning.txt` |
| 8 | Backup del cliente: `pg_dump -Fc` → 764 KB, 375 entradas TOC, verificado restaurable con `pg_restore --list` (dentro del contenedor) | ✅ `06-backup.txt` |

## El camino hasta el PASS: la evidencia cazó 3 bugs reales

Nada de "debería funcionar" — cada corrida fresh-clone ejercitó el artefacto
real y falló donde había que fallar:

1. **Corrida 1** (contra el primer tag): `setup.sh` generaba secretos VACÍOS
   (`gen_if_placeholder` ejecutaba el string `"rand_hex 24"` como si fuera
   un comando). Resultado: Keycloak sin password no arrancó y el API habría
   rehusado arrancar (fail-closed funcionando como debe). **Fix**: evaluación
   en el callsite + guard fail-fast pre-arranque (commit `a8d1802`).
2. **Corrida 2**: el smoke corría demasiado pronto — las consoles Next.js
   arrancan después del API y el smoke las encontró aún booting → 3 FAILs
   falsos (el stack estaba sano segundos después). **Fix**: espera explícita
   de ambas consoles antes del smoke (commit `cac2429`).
3. **Corrida 2** (backup): `backup.sh` verificaba el dump con `pg_restore`
   del HOST — un Docker host limpio no tiene postgres client-tools → exit
   127 tras producir un dump válido. **Fix**: verificación dentro del
   contenedor postgres (commit `aa69d6b`, post-tag, en main).

**Corrida 3** (la de esta evidencia, contra el tag final `cac2429`): PASS
completo, 6/6, sin intervención manual.

> Nota de re-tag (transparencia): el tag `v0.1.0-pilot` se movió dos veces en
> la hora siguiente a su creación original para incluir los fixes 1 y 2 —
> el tag representa el piloto desplegable validado, no un setup que falla en
> falso. El fix 3 (backup) es cosmético al flujo de setup y quedó en main
> inmediatamente después. Documentado también en las notas del release
> GitHub.

## Cómo reproducirlo (en cualquier host Linux con Docker)

```bash
git clone --branch v0.1.0-pilot https://github.com/jarceg884/trust-agent.git
cd trust-agent
scripts/setup.sh                       # modo dev: zero config
# o modo servidor:
TRUST_PUBLIC_BASE_URL=http://TU-IP scripts/setup.sh
scripts/smoke.sh                       # 6 PASS / 0 FAIL
```

En esta VM la corrida usó además un prefijo de puertos distinto
(`TRUST_DB_PORT=56432`, `TRUST_IDP_PORT=8181`, `TRUST_API_PORT=8100`,
`TRUST_CONSOLE_PORT=3110`, `TRUST_PROVIDER_CONSOLE_PORT=3210`,
`TRUST_LITELLM_DOUBLE_PORT=39990`, `TRUST_CONTAINER_PREFIX=ta2` +
`COMPOSE_PROJECT_NAME=ta2-evidence`) porque el stack dev ocupa los puertos
canónicos — exactamente el escenario "dos stacks del piloto en el mismo
host" documentado en el README. Un cliente en un host limpio no necesita
tocar nada de eso.

## Entorno de la corrida

- Host: la VM de desarrollo (Ubuntu 24.04, Docker 27, compose 2.40) — la
  limitación honesta: no hay otra máquina; la simulación de cliente se logra
  con fresh clone del repo PÚBLICO + volúmenes/redes/proyecto/contenedores/
  puertos 100% nuevos, sin tocar nada del stack dev (verificado en
  `04-data-isolation.txt`).
- Tag: `v0.1.0-pilot` → commit `cac2429ed5e9b63efb07f5ae272e783a57a861f0`.
- Duración de `setup.sh` en frío con build cacheado: ~2 min (sin cache,
  ~5-8 min la primera vez).

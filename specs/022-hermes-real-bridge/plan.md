# Plan técnico — Spec 022 (hermes-real-bridge)

## Layout de archivos

```
platform/desktop-client/hermes/plugins/trust-agent-governance/
├── plugin.yaml                  # (dueño: t_f99e12ab — solo lectura para nosotros hasta el cableado final)
├── __init__.py                  # (dueño: t_f99e12ab; cableado final de enroll/enforcement: nosotros)
├── enroll.py                    # NUEVO (esta spec): /trust-enroll + keystore + token store
├── enforcement.py               # NUEVO (esta spec): verificador de bundle + veto pre_tool_call +
│                                #   fail-closed + aviso restricted por pre_llm_call + veto-counts
└── trust_agent_policy_pubkey.pem# clave pública pineada (la instala el despliegue del perfil)
```

Estado en disco del perfil (`$HERMES_HOME/trust-agent/`):
- `device_key.pem` / `device_key.pub.pem` (0600 la privada) — keypair del dispositivo.
- `device-token.json` (0600) — `{device_id, token, enrolled_at, org_code, tenant_slug}`.
- `governance-bundle.jws` — caché del bundle (JWS compacto; contrato compartido con el núcleo v2).
- `jti-cache.json` — últimos jtis vistos (anti-replay).
- `veto-counts.json` — conteo de vetos por tool (para attestation futura, REQ-GOH-017).
- `plugin.log` — log local append-only de eventos de gobernanza (sync, rechazos, vetos, modos).

## Módulos

### enroll.py
- `load_or_create_keypair(home) -> (priv_pem, pub_pem, fingerprint)` — RSA-2048, PKCS#8/SPKI, 0600.
- `cmd_trust_enroll(raw_args: str) -> str` — handler del comando (§5 de la spec).
- `load_device_token() -> dict | None` — archivo `device-token.json` > env `TRUST_DEVICE_TOKEN`.
- `register(ctx)` — registra el comando `/trust-enroll`.
- HTTP con `urllib` (sin dependencias nuevas), TLS verificado (nunca `verify=False`).

### enforcement.py
- `verify_bundle(jws, *, pubkey_pem, device_id, jti_cache) -> dict` — RS256 pinneado, aud, exp, jti; lanza `BundleRejected(reason)`.
- `GovernanceState` (singleton por proceso):
  - `evaluate()` → modo `normal|restricted` + causa + payload vigente; dispara `refresh()` si el caché falta/expiró (UN GET al endpoint; 401/403 → invalida caché + restricted; red caída → caché dentro de gracia).
  - `on_pre_tool_call(tool, **kw)` → `{"action":"block","message":...}` o `None`.
  - `on_pre_llm_call(**kw)` → `{"context": ...}` SOLO en restricted (normal → None).
- `register(ctx)` — registra los dos hooks.
- Tools internas siempre permitidas: `governance_status` (+ las que registre este paquete).

### Cableado final en `__init__.py` (tras convergencia del Inc. 1)
```python
try:
    from . import enroll, enforcement
    enroll.register(ctx); enforcement.register(ctx)
except ImportError:
    pass
```

## Perfil real de validación
- Perfil `trust-e2e` (CLI real de Hermes; home aislado).
- `config.yaml`: `model.base_url` → gateway LiteLLM del tenant, `api_key: ${TRUST_USER_VIRTUAL_KEY}`, sin `fallback_model`; `plugins.enabled: [trust-agent-governance]`.
- Plugin copiado al home del perfil + pubkey pineada.

## Tests
- Unitarios (pytest, venv de platform): `platform/tests/test_hrb_enforcement.py` — AC-HRB-003/004/005 con claves generadas en tmp (sin red).
- Live (evidencia en docs): AC-HRB-001/006 + enforcement en vivo AC-HRB-004(c) + suite Hermes AC-HRB-007.

## Riesgos / mitigaciones
- **Colisión con t_f99e12ab**: solo archivos nuevos; cableado al final sobre su versión final; hotspot declarado en ambas tarjetas.
- **Device token 15 min**: la demo live cabe en el TTL; restricción documentada (D-HRB-03).
- **Suite Hermes larga**: correr en background con heartbeat; si el tiempo excede, subconjunto documentado + suite completa agendada.
- **Rebuild de imagen compartida**: coordinar en el hilo de t_f99e12ab antes de `docker compose build/up`.

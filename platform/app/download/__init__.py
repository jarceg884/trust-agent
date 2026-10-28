"""Download Hermes por tenant (spec 023, t_526030f3) — seam link→app.

Endpoint público `GET /download/{tenant_slug}` (+ `/bundle`), bundle
white-label real (plugin trust-agent-governance de la spec 022 + instalador
de Hermes COMPLETO + binding de org) y gestión del link desde el Admin
Console del cliente (`GET/PUT /api/download-link`, permiso
``download_link.manage``). Ver specs/023-hermes-download/spec.md.
"""

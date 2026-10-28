"""Provider plane (spec 025, mandato Miller): super-user del Provider Console.

El operador del proveedor es el ÚNICO actor fuera del multi-tenant. Su
identidad, sesiones y audit viven en el plano proveedor ``ops`` (sin RLS por
diseño, D-DB-01). Ver specs/025-provider-superuser/spec.md.
"""

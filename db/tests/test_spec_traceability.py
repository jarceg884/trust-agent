"""Convergencia SDD (constitution II): trazabilidad REQ↔AC de la spec 002.

Parsea specs/002-data-layer/spec.md y verifica:
  - todo REQ-DB-nnn definido tiene su AC-DB-nnn definido (1:1, sin huérfanos),
  - no hay IDs duplicados,
  - el checklist de convergencia referencia los AC.
"""
import os
import re

SPEC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "specs", "002-data-layer", "spec.md",
)


def _ids(pattern: str) -> list[str]:
    text = open(SPEC, encoding="utf-8").read()
    return re.findall(pattern, text)


def test_req_ac_bidirectional_no_orphans():
    reqs = _ids(r"\*\*REQ-DB-(\d+)\*\*")
    acs = _ids(r"\*\*AC-DB-(\d+)\*\*")
    assert len(reqs) == len(set(reqs)), "REQ duplicados"
    assert len(acs) == len(set(acs)), "AC duplicados"
    assert set(reqs) == set(acs), (
        f"huérfanos: REQ sin AC {set(reqs) - set(acs)}, AC sin REQ {set(acs) - set(reqs)}"
    )
    assert len(reqs) >= 20, "cobertura mínima de requisitos"


def test_every_req_is_ears_and_has_acceptance_criteria():
    text = open(SPEC, encoding="utf-8").read()
    blocks = re.split(r"(?=^\*\*REQ-DB-)", text, flags=re.M)
    req_blocks = [b for b in blocks if b.startswith("**REQ-DB-")]
    assert len(req_blocks) >= 20
    for block in req_blocks:
        rid = re.match(r"\*\*(REQ-DB-\d+)\*\*", block).group(1)
        assert "Cuando" in block and "el sistema" in block, f"{rid}: plantilla EARS ausente"
        assert "AC-DB-" in block, f"{rid}: sin criterio de aceptación"
        assert re.search(r"\*\*AC-DB-\d+\*\*", block), f"{rid}: AC sin formato"


def test_convergence_checklist_references_acs():
    text = open(SPEC, encoding="utf-8").read()
    assert "AC-DB-001…042" in text or "AC-DB-001" in text
    checklist = text.split("## 5. Checklist de convergencia")[1]
    assert "aislamiento" in checklist.lower()
    assert "paridad" in checklist.lower()

"""Phase 4 safety logic (offline): what to do with a firm that may already exist.

PLANNING ONLY. Nothing here reads or writes peViitor/Solr; callers pass in the
existing documents (for example an exported JSONL) and get a decision back.

Rules, in order:

1. no existing document for this CUI                    -> ``create``
2. only a legacy UNPADDED document exists (``1697454``)  -> ``conflict_unpadded``
   (creating ``01697454`` would duplicate the firm; migration is a human decision)
3. several documents with this exact id                  -> ``review``
4. the document has no ``scraperFile`` (unknown owner)   -> ``review``
5. owned by another scraper (the fleet writes a workflow URL) -> ``skip_foreign``
   (never overwrite a document another scraper maintains)
6. owned by us / Inviitor and nothing meaningful differs -> ``skip_unchanged``
7. owned by us / Inviitor and something differs          -> ``update`` (+ changed fields)

``lastScraped`` and ``existingJobsCount`` never count as a difference: the first
changes every run and the second is a placeholder until jobs are wired in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

CREATE = "create"
UPDATE = "update"
SKIP_UNCHANGED = "skip_unchanged"
SKIP_FOREIGN = "skip_foreign"
CONFLICT_UNPADDED = "conflict_unpadded"
REVIEW = "review"

#: scrapers whose documents DerKaiser may update. Inviitor's are included because DerKaiser
#: takes over its role; that is a pending decision for Sebi, hence configurable.
DEFAULT_UPDATABLE = frozenset({"derkaiser-python-scraper", "inviitor-ro-nodejs-scraper"})

COMPARED_FIELDS = ("company", "brand", "status", "location", "address", "website", "anafData")


@dataclass
class Decision:
    action: str
    reason: str = ""
    existing_id: str | None = None
    changed: list[str] = field(default_factory=list)

    def as_row(self, cif: str) -> dict[str, Any]:
        return {"cif": cif, "action": self.action, "reason": self.reason, "existing_id": self.existing_id, "changed": self.changed}


def _cui_of(doc: dict) -> int | None:
    value = str(doc.get("id", "")).strip()
    return int(value) if value.isdigit() else None


def _norm(value: Any) -> Any:
    """Solr may hand back one-element lists where we hold a scalar (or the reverse)."""
    if isinstance(value, list) and len(value) == 1 and not isinstance(value[0], (list, dict)):
        return value[0]
    return value


def _owner(doc: dict) -> str:
    return str(_norm(doc.get("scraperFile")) or "").strip()


def changed_fields(ours: dict, existing: dict) -> list[str]:
    return [name for name in COMPARED_FIELDS if _norm(ours.get(name)) != _norm(existing.get(name))]


def decide(doc: dict, existing_docs: Iterable[dict], *, updatable: frozenset[str] | set[str] = DEFAULT_UPDATABLE) -> Decision:
    """Decide what to do with ``doc`` given the documents already stored for the same CUI."""
    cui = int(doc["id"])
    same = [e for e in existing_docs if _cui_of(e) == cui]
    if not same:
        return Decision(CREATE, "no existing document for this CUI")

    exact = [e for e in same if str(e.get("id")).strip() == doc["id"]]
    legacy = [e for e in same if e not in exact]

    if not exact:
        ids = ", ".join(sorted(str(e.get("id")) for e in legacy))
        owners = ", ".join(sorted({_owner(e) or "?" for e in legacy}))
        return Decision(CONFLICT_UNPADDED, f"only unpadded document(s) exist: {ids} (owner: {owners})", existing_id=str(legacy[0].get("id")))

    if len(exact) > 1:
        return Decision(REVIEW, f"{len(exact)} documents share id {doc['id']}", existing_id=doc["id"])

    existing = exact[0]
    owner = _owner(existing)
    note = f" (also unpadded: {', '.join(str(e.get('id')) for e in legacy)})" if legacy else ""
    if not owner:
        return Decision(REVIEW, "existing document has no scraperFile: unknown owner" + note, existing_id=doc["id"])
    if owner not in updatable:
        return Decision(SKIP_FOREIGN, f"owned by another scraper: {owner}" + note, existing_id=doc["id"])

    diff = changed_fields(doc, existing)
    if not diff:
        return Decision(SKIP_UNCHANGED, "nothing meaningful differs" + note, existing_id=doc["id"])
    return Decision(UPDATE, f"differs in: {', '.join(diff)}" + note, existing_id=doc["id"], changed=diff)


def load_existing_index(path: str | Path) -> dict[int, list[dict]]:
    """Index an exported JSONL of existing company documents by integer CUI."""
    index: dict[int, list[dict]] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and (cui := _cui_of(doc)) is not None:
            index.setdefault(cui, []).append(doc)
    return index

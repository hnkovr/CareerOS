"""Compare two CV documents: added / removed / rewritten bullets (by provenance) and keywords."""

from __future__ import annotations

from careeros.modules.cv.schemas import Bullet, BulletDiff, CVComparison, CVDocument


def _index(doc: CVDocument) -> dict[tuple[str, frozenset[str]], list[tuple[str, Bullet]]]:
    idx: dict[tuple[str, frozenset[str]], list[tuple[str, Bullet]]] = {}
    for section, group, b in doc.all_bullets():
        idx.setdefault((section, frozenset(b.derived_from)), []).append((group, b))
    return idx


def compare_documents(
    a: CVDocument, b: CVDocument, *, label_a: str = "a", label_b: str = "b"
) -> CVComparison:
    ia, ib = _index(a), _index(b)
    added: list[BulletDiff] = []
    removed: list[BulletDiff] = []
    rewritten: list[BulletDiff] = []
    unchanged = 0

    for key, items_b in ib.items():
        items_a = ia.get(key)
        if not items_a:
            added.extend(
                BulletDiff(group=g, text_a=None, text_b=bl.text, derived_from=sorted(key[1]))
                for g, bl in items_b
            )
            continue
        for (g, bl_b), (_, bl_a) in zip(items_b, items_a, strict=False):
            if bl_a.text.strip() == bl_b.text.strip():
                unchanged += 1
            else:
                rewritten.append(
                    BulletDiff(
                        group=g, text_a=bl_a.text, text_b=bl_b.text, derived_from=sorted(key[1])
                    )
                )
        if len(items_b) > len(items_a):
            added.extend(
                BulletDiff(group=g, text_a=None, text_b=bl.text, derived_from=sorted(key[1]))
                for g, bl in items_b[len(items_a) :]
            )
    for key, items_a in ia.items():
        items_b = ib.get(key)
        if not items_b:
            removed.extend(
                BulletDiff(group=g, text_a=bl.text, text_b=None, derived_from=sorted(key[1]))
                for g, bl in items_a
            )
        elif len(items_a) > len(items_b):
            removed.extend(
                BulletDiff(group=g, text_a=bl.text, text_b=None, derived_from=sorted(key[1]))
                for g, bl in items_a[len(items_b) :]
            )

    ka, kb = set(a.keywords), set(b.keywords)
    return CVComparison(
        a=label_a,
        b=label_b,
        added=added,
        removed=removed,
        rewritten=rewritten,
        unchanged=unchanged,
        keywords_only_a=sorted(ka - kb),
        keywords_only_b=sorted(kb - ka),
        sections_a=[str(s) for s in a.sections],
        sections_b=[str(s) for s in b.sections],
    )

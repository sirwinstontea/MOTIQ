"""Proof that profile numbers really come from the downloaded official documents.

Every ``src`` block in a profile (``doc`` + ``quote`` or ``section``/``contains``) is checked against
the extracted text of that document. Used by ``tests/test_provenance.py`` and by the CLI command
``hcc verify-profiles``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

from .profiles import DEFAULT_PROFILE_DIR, PROJECT_ROOT

_PAGE_RE = re.compile(r"=====PAGE (\d+)=====")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class SourceCheck:
    profile: str
    path: str  # dotted path inside the YAML
    doc: str
    ok: bool
    detail: str
    page: int | None = None


def _iter_sources(node: Any, path: str = "") -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if (k == "src" or str(k).endswith("_src")) and isinstance(v, dict) and "doc" in v:
                yield p, v
            elif k == "quotes" and isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, dict) and "doc" in item:
                        yield f"{p}[{i}]", item
            else:
                yield from _iter_sources(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _iter_sources(v, f"{path}[{i}]")


def _doc_text(docs: dict, doc_id: str) -> str | None:
    d = docs.get(doc_id)
    if not d:
        return None
    if "extracted_text" in d:
        return d["extracted_text"]
    rel = d.get("text")
    if rel:
        p = PROJECT_ROOT / rel
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None


def _all_docs(profile_dir: Path) -> dict:
    docs: dict = {}
    for f in profile_dir.glob("*.yaml"):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        docs.update(data.get("documents", {}) or {})
    return docs


def verify_profile_file(path: Path, docs: dict | None = None) -> list[SourceCheck]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = docs if docs is not None else _all_docs(path.parent)
    results: list[SourceCheck] = []
    for ppath, src in _iter_sources({k: v for k, v in data.items() if k != "documents"}):
        doc_id = src["doc"]
        text = _doc_text(docs, doc_id)
        if text is None:
            results.append(SourceCheck(path.name, ppath, doc_id, False, "document text not available"))
            continue
        flat = _norm(text)
        if "quote" in src:
            q = _norm(str(src["quote"]))
            idx = flat.find(q)
            if idx < 0:
                results.append(SourceCheck(path.name, ppath, doc_id, False, f"quote not found: {q!r}"))
                continue
            page = None
            # locate page number on the whitespace-preserving text (best effort)
            raw = text
            raw_flat_positions = _locate_page(raw, q)
            page = raw_flat_positions
            results.append(SourceCheck(path.name, ppath, doc_id, True, "quote found", page))
        elif "contains" in src:
            region = flat
            if "section" in src and src["section"]:
                start_a, end_a = src["section"]
                s = flat.find(_norm(start_a))
                if s < 0:
                    results.append(SourceCheck(path.name, ppath, doc_id, False, f"section start not found: {start_a!r}"))
                    continue
                e = flat.find(_norm(end_a), s + len(start_a))
                region = flat[s : e if e > 0 else len(flat)]
            missing = [c for c in src["contains"] if _norm(str(c)) not in region]
            if missing:
                results.append(SourceCheck(path.name, ppath, doc_id, False, f"values not found in section: {missing}"))
            else:
                results.append(SourceCheck(path.name, ppath, doc_id, True, f"{len(src['contains'])} values found"))
        else:
            results.append(SourceCheck(path.name, ppath, doc_id, False, "src needs 'quote' or 'contains'"))
    return results


def _locate_page(text: str, norm_quote: str) -> int | None:
    """Page number (1-based, PDF page) of the first occurrence of a normalized quote."""
    pages = list(_PAGE_RE.finditer(text))
    if not pages:
        return None
    for i, m in enumerate(pages):
        end = pages[i + 1].start() if i + 1 < len(pages) else len(text)
        if norm_quote in _norm(text[m.end() : end]):
            return int(m.group(1))
    return None


def verify_all(profile_dir: Path | None = None) -> list[SourceCheck]:
    d = Path(profile_dir) if profile_dir else DEFAULT_PROFILE_DIR
    docs = _all_docs(d)
    out: list[SourceCheck] = []
    for f in sorted(d.glob("*.yaml")):
        out.extend(verify_profile_file(f, docs))
    return out

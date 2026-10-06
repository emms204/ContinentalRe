"""IMPL-20260929-04 guard: no cedant-specific names or rules in the shared core.

Fails when
  (1) a cedant / broker name appears anywhere (code, strings or comments) in a
      shared module, or
  (2) a string that exists only in an adapter's hooks (alias extras, band /
      layout vocabulary, class-label extras, tab-typing tokens, table-vocab
      terms) appears as a string literal in a shared module, or
  (3) the shared alias tables carry an 'OUR SHARE' (cedant-share) name.

Cedant rules belong in cre_cleaner/adapters/<cedant>.py (Emmanuel: cedant logic
must not be merged).
"""
from __future__ import annotations

import ast
import inspect
import io
import re
import tokenize
from pathlib import Path

import cre_cleaner
from cre_cleaner.adapters import ADAPTERS
from cre_cleaner.core import map_columns as mc
from cre_cleaner.core.class_labels import _CLASS_MAP
from cre_cleaner.core.detect import GENERIC_SHEET_RULES
from cre_cleaner.core.normalize import normalize_header

PKG = Path(cre_cleaner.__file__).parent

# Shared (cedant-neutral) modules.
SHARED = sorted(
    [p for p in (PKG / "core").glob("*.py")]
    + [p for p in (PKG / "io").glob("*.py")]
    + [PKG / n for n in ("config.py", "models.py", "pipeline.py", "cli.py", "api.py")
       if (PKG / n).exists()]
)
# Not scanned: cre_cleaner/paths.py — data locations only (the demo app's
# sample folder data/raw/newdata/<cedant>/<broker>/2025), no mapping rules.
# Adapter infrastructure shared by all cedants: names only (no rules either).
SHARED_ADAPTER_INFRA = [PKG / "adapters" / "base.py", PKG / "adapters" / "monthly_files.py"]

CEDANT_NAMES = [
    "AIICO", "UNITRUST", "AXA", "MANSARD", "LASACO", "HEIRS", "JOMOLA", "CUSTODIAN",
    "NEM", "ROYAL EXCHANGE", "MUTUAL BENEFITS", "CHI", "SCIB", "ARK", "FEYBIL",
    "JORDANS", "UAIB", "AON", "HIB",
]
_NAME_RE = re.compile(r"(?<![A-Z0-9])(" + "|".join(re.escape(n) for n in CEDANT_NAMES) + r")(?![A-Z0-9])")


def _texts(path: Path):
    """(line, text) of every string and comment token."""
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT, tokenize.NAME):
            yield tok.start[0], tok.string


def test_no_cedant_names_in_shared_modules():
    hits = []
    for p in SHARED + SHARED_ADAPTER_INFRA:
        for line, text in _texts(p):
            for m in _NAME_RE.finditer(text.upper().replace("_", " ")):
                hits.append(f"{p.relative_to(PKG)}:{line}: {m.group(1)!r} in {text[:80]!r}")
    assert not hits, "cedant names in shared code:\n" + "\n".join(hits)


def _generic_vocab() -> set:
    out = set()
    for tbl in (mc.PREMIUM_ALIASES, mc.CLAIMS_ALIASES):
        for v in tbl.values():
            out.update(normalize_header(a) for a in v)
    r = mc.GENERIC_PREMIUM_RULES
    for name in ("PPN_EXACT", "SI_EXACT", "PREM_EXACT", "RET_BAND_WORDS", "FAC_BAND_WORDS",
                 "SUR_BAND_WORDS", "PPN_PCT_BAND_WORDS"):
        out.update(getattr(r, name))
    out.update(k.upper() for k in _CLASS_MAP)
    out.update(GENERIC_SHEET_RULES.ost_tokens)
    return out


# Generic words an adapter may also list, that the shared core legitimately uses
# for its own generic purpose (output schema headers, share-header detection,
# 'tab carries no class' words). Each is a common bordereau word, not a
# cedant alias.
GENERIC_WORDS = {
    "AMOUNT", "RATE", "PCNT", "DESCRIPTION",          # generic header words
    "FAC PREMIUM", "FAC SUM INSURED",                 # upload schema headers (config)
    "OS", "PROD", "PRODUCTION", "LISTING",            # detect: tab words carrying no class
}


def _adapter_only_strings() -> set:
    """Every vocabulary string an adapter declares that the generic core lacks."""
    found = set()

    def add(v):
        if isinstance(v, str):
            found.add(normalize_header(v) or v.upper())
        elif isinstance(v, dict):
            for k, x in v.items():
                add(k)
                add(x)
        elif isinstance(v, (list, tuple, set, frozenset)):
            for x in v:
                add(x)

    for cls in ADAPTERS.values():
        ad = cls()
        add(list(ad.premium_alias_extra.values()))
        add(list(ad.claims_alias_extra.values()))
        add(list(ad.class_label_extra.keys()))
        add(ad.class_label_exact_only)
        st = ad.sheet_type_rules
        add(st.ost_tokens)
        add(st.dump_tokens)
        rules = ad.premium_layout_rules
        for name, val in vars(type(rules)).items():
            if name.isupper():
                add(val)
    # Module-level vocabulary of the per-cedant modules (e.g. share-label headers).
    for mod_path in (PKG / "adapters").glob("*.py"):
        if mod_path in SHARED_ADAPTER_INFRA or mod_path.name == "__init__.py":
            continue
        tree = ast.parse(mod_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id.isupper() for t in node.targets
            ):
                for c in ast.walk(node.value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str) and len(c.value) >= 2:
                        add(c.value)
    return {s for s in found - _generic_vocab() - GENERIC_WORDS
            if s and len(s) >= 2 and not s.isdigit()}


def _shared_literals(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docs:
            out.add(normalize_header(node.value) or node.value.upper())
    return out


def test_adapter_only_aliases_absent_from_shared_modules():
    adapter_only = _adapter_only_strings()
    assert adapter_only, "guard found no adapter vocabulary — test is broken"
    hits = []
    for p in SHARED:
        lits = _shared_literals(p)
        for s in sorted(adapter_only & lits):
            hits.append(f"{p.relative_to(PKG)}: {s!r}")
    assert not hits, "adapter-only vocabulary in shared code:\n" + "\n".join(hits)


def test_shared_alias_tables_have_no_our_share_names():
    bad = [a for tbl in (mc.PREMIUM_ALIASES, mc.CLAIMS_ALIASES) for v in tbl.values() for a in v
           if "OUR SHARE" in normalize_header(a)]
    assert not bad, bad


def test_guard_detects_a_planted_cedant_rule(tmp_path):
    """Self-test: the scanners do catch a cedant name / adapter alias."""
    planted = tmp_path / "planted.py"
    planted.write_text('X = ["AIICO S NET LIABILITY"]  # Unitrust rule\n', encoding="utf-8")
    names = [t for _l, t in _texts(planted) if _NAME_RE.search(t.upper().replace("_", " "))]
    assert names
    assert "AIICO S NET LIABILITY" in _adapter_only_strings() & _shared_literals(planted)

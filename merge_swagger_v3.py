#!/usr/bin/env python3
"""
merge_swagger_v3.py  -  CP Catalog API 360 canonical OpenAPI merge engine
=========================================================================
Resolve-first (not rename-first) merge of many OpenAPI/Swagger specs into ONE
canonical spec per business domain, for CP Catalog API 360 ingestion.

Replaces the v2 "auto-suffix on any collision" behavior with:

  1) YAML PRE-VALIDATOR
       - converts leading TAB indentation to spaces (recovers files v2 SKIPPED)
       - never silently drops: every skip is logged with a reason
  2) HASH-BASED SCHEMA DEDUP
       - structural fingerprint; byte-identical schemas collapse to ONE copy
  3) CANONICAL ENVELOPE REGISTRY
       - ApiError / SWPApiError / ErrorInfo / *ErrorResponse / *ErrorSource /
         CommonError* -> single canonical "Error" (refs rewritten)
  4) SECURITY SCHEME NORMALIZER
       - OAuth2 / oauth2 / oAuth2 (case variants) folded to one global scheme
  5) CONFLICT CLASSIFIER
       - DUPLICATE_IDENTICAL  -> merged silently
       - NAME_COLLISION_ONLY  -> canonical-mapped (envelopes)
       - STRUCTURAL_CONFLICT  -> kept as <Name>__<source>, flagged for review
       - emitted to _conflict_report.json (UI-ready for CP Catalog)
  6) DETERMINISTIC OUTPUT + catalog metadata + canonical_map.json

Usage:
    pip install pyyaml
    python merge_swagger_v3.py --src C:\\SEI\\API-SPEC --out C:\\SEI\\merged --yaml

CRITICAL: --out MUST be OUTSIDE --src, or the engine re-ingests its own output
(this is what created the phantom 17th 'out' domain with 1872 schemas).
"""

import argparse, json, sys, copy, re, hashlib
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

COMP_BUCKETS = ["schemas", "responses", "parameters", "examples",
                "requestBodies", "headers", "securitySchemes", "links", "callbacks"]

# Envelope schemas that should collapse to one canonical "Error" concept.
CANON_ERROR_PAT = re.compile(
    r"^(api)?error(info|response)?$|errorresponse$|commonerror|"
    r"errorsource$|apiresponsewarning$|^errors$",
    re.IGNORECASE,
)
CANON_ERROR_NAME = "Error"


# ---------------------------------------------------------------- helpers ----
def json_safe(obj):
    import datetime
    from decimal import Decimal
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type not serializable: {type(obj)}")


def _stringify_keys(o):
    if isinstance(o, dict):
        return {str(k): _stringify_keys(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_stringify_keys(v) for v in o]
    return o


def fingerprint(obj) -> str:
    """Structural hash used for identity comparison (order-independent)."""
    return hashlib.sha256(
        json.dumps(_stringify_keys(obj), sort_keys=True, default=json_safe).encode()
    ).hexdigest()


def is_openapi(d) -> bool:
    return isinstance(d, dict) and ("openapi" in d or "swagger" in d) and "paths" in d


# ---------------------------------------------------- YAML pre-validation ----
def detab(text: str) -> str:
    """Convert leading tabs on each line to two spaces (safe YAML repair)."""
    out = []
    for line in text.splitlines():
        i = 0
        while i < len(line) and line[i] == "\t":
            i += 1
        out.append("  " * i + line[i:])
    return "\n".join(out)


def load_spec(p: Path, report):
    text = p.read_text(encoding="utf-8-sig")
    suffix = p.suffix.lower()
    if suffix in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("pyyaml not installed: pip install pyyaml")
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as e:
            if "\t" in text:                       # recover tab-indented files
                repaired = yaml.safe_load(detab(text))
                report.append(f"REPAIRED {p.name}: tab indentation auto-fixed")
                return repaired
            raise
    return json.loads(text)


# -------------------------------------------------------- ref rewriting ------
def _rewrite_refs(node, fn):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                node[k] = fn(v)
            else:
                _rewrite_refs(v, fn)
    elif isinstance(node, list):
        for item in node:
            _rewrite_refs(item, fn)


def normalize_swagger2(spec: dict) -> dict:
    spec = copy.deepcopy(spec)
    if "definitions" in spec:
        comps = spec.setdefault("components", {})
        comps.setdefault("schemas", {}).update(spec.pop("definitions"))
        _rewrite_refs(spec, lambda r: r.replace("#/definitions/", "#/components/schemas/"))
    if "securityDefinitions" in spec:                 # swagger 2.0 security
        comps = spec.setdefault("components", {})
        comps.setdefault("securitySchemes", {}).update(spec.pop("securityDefinitions"))
        _rewrite_refs(spec, lambda r: r.replace(
            "#/securityDefinitions/", "#/components/securitySchemes/"))
    return spec


# ----------------------------------------------------------- merge core ------
def merge_domain(files, domain, classifier):
    report = []
    merged = {
        "openapi": "3.0.3",
        "info": {"title": f"BBH CP - {domain}", "version": "1.0.0",
                 "description": f"Canonical merged OpenAPI spec for the {domain} domain."},
        "paths": {},
        "components": {b: {} for b in COMP_BUCKETS},
        "tags": [],
    }
    seen_tags = set()
    canonical_map = {}     # original ref -> canonical ref (this domain)

    for f in sorted(files):
        try:
            spec = normalize_swagger2(load_spec(f, report))
        except Exception as e:
            report.append(f"SKIP {f.name}: {e}")
            classifier.append({"file": f.name, "domain": domain,
                               "conflict_type": "PARSE_ERROR", "detail": str(e)})
            continue
        if not is_openapi(spec):
            report.append(f"SKIP {f.name}: not an OpenAPI/Swagger doc")
            continue

        stem = f.stem.replace(" ", "_")
        rename_map = {}

        comps = spec.get("components", {}) or {}
        for bucket in COMP_BUCKETS:
            for name, defn in (comps.get(bucket) or {}).items():
                tgt = merged["components"][bucket]

                # (A) security schemes: case-normalize + fold equivalents
                if bucket == "securitySchemes":
                    canon_sec = "OAuth2" if name.lower() == "oauth2" else name
                    if canon_sec not in tgt:
                        tgt[canon_sec] = defn
                    if canon_sec != name:
                        rename_map[f"#/components/securitySchemes/{name}"] = \
                            f"#/components/securitySchemes/{canon_sec}"
                        classifier.append({"file": f.name, "domain": domain, "bucket": bucket,
                                           "original": name, "canonical": canon_sec,
                                           "conflict_type": "NAME_COLLISION_ONLY",
                                           "resolution": "MERGED"})
                    continue

                # (B) canonical error envelopes -> single "Error"
                if bucket == "schemas" and CANON_ERROR_PAT.search(re.sub(r"__.*$", "", name)):
                    if CANON_ERROR_NAME not in tgt:
                        tgt[CANON_ERROR_NAME] = defn
                    rename_map[f"#/components/schemas/{name}"] = \
                        f"#/components/schemas/{CANON_ERROR_NAME}"
                    if name != CANON_ERROR_NAME:
                        classifier.append({"file": f.name, "domain": domain, "bucket": bucket,
                                           "original": name, "canonical": CANON_ERROR_NAME,
                                           "conflict_type": "NAME_COLLISION_ONLY",
                                           "resolution": "MERGED"})
                    continue

                # (C) general dedup by structural fingerprint
                if name not in tgt:
                    tgt[name] = defn
                elif fingerprint(tgt[name]) == fingerprint(defn):
                    pass  # DUPLICATE_IDENTICAL -> collapse silently
                else:
                    new_name = f"{name}__{stem}"
                    while new_name in tgt and fingerprint(tgt[new_name]) != fingerprint(defn):
                        new_name += "_x"
                    tgt[new_name] = defn
                    rename_map[f"#/components/{bucket}/{name}"] = \
                        f"#/components/{bucket}/{new_name}"
                    classifier.append({"file": f.name, "domain": domain, "bucket": bucket,
                                       "original": name, "canonical": new_name,
                                       "conflict_type": "STRUCTURAL_CONFLICT",
                                       "resolution": "KEPT_SUFFIXED_REVIEW"})
                    report.append(f"STRUCTURAL_CONFLICT components.{bucket}: "
                                  f"{name} -> {new_name} ({stem})")

        canonical_map.update(rename_map)
        if rename_map:
            _rewrite_refs(spec, lambda r: rename_map.get(r, r))

        # paths
        for path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            if path not in merged["paths"]:
                merged["paths"][path] = item
            else:
                for method, op in item.items():
                    if method not in merged["paths"][path]:
                        merged["paths"][path][method] = op
                    elif fingerprint(merged["paths"][path][method]) != fingerprint(op):
                        if isinstance(op, dict) and "operationId" in op:
                            op = copy.deepcopy(op)
                            op["operationId"] = f"{op['operationId']}__{stem}"
                        classifier.append({"file": f.name, "domain": domain,
                                           "path": path, "method": method,
                                           "conflict_type": "PATH_METHOD_CLASH",
                                           "resolution": "SUFFIXED_OPERATIONID"})
                        report.append(f"PATH_METHOD_CLASH {path}[{method}] ({stem})")

        for t in spec.get("tags", []) or []:
            if isinstance(t, dict) and t.get("name") and t["name"] not in seen_tags:
                seen_tags.add(t["name"]); merged["tags"].append(t)

    merged["components"] = {b: v for b, v in merged["components"].items() if v}
    if not merged["components"]:
        merged.pop("components")
    if not merged["tags"]:
        merged.pop("tags")
    return merged, report, canonical_map


# ---------------------------------------------------------------- main -------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--yaml", action="store_true")
    args = ap.parse_args()

    src, out = Path(args.src).resolve(), Path(args.out).resolve()
    if out == src or src in out.parents:
        print(f"ERROR: --out ({out}) is inside --src ({src}). "
              f"Choose an output folder OUTSIDE the source tree.")
        sys.exit(2)
    out.mkdir(parents=True, exist_ok=True)

    domains = [d for d in src.iterdir() if d.is_dir()]
    if not domains:
        print("No domain subfolders found under", src); sys.exit(1)

    grand, summary, classifier, all_canon = [], [], [], {}
    for d in sorted(domains):
        files = [p for p in d.rglob("*") if p.suffix.lower() in (".json", ".yaml", ".yml")]
        if not files:
            print(f"-- {d.name}: no specs, skipped"); continue
        merged, report, cmap = merge_domain(files, d.name, classifier)
        ext = "yaml" if args.yaml else "json"
        target = out / f"{d.name}.openapi.{ext}"
        if args.yaml:
            target.write_text(yaml.safe_dump(merged, sort_keys=False, allow_unicode=True),
                              encoding="utf-8")
        else:
            target.write_text(json.dumps(merged, indent=2, default=json_safe),
                              encoding="utf-8")
        npaths = len(merged.get("paths", {}))
        nschemas = len(merged.get("components", {}).get("schemas", {}))
        struct = len([r for r in report if r.startswith("STRUCTURAL")])
        repaired = len([r for r in report if r.startswith("REPAIRED")])
        print(f"OK {d.name}: {len(files)} files -> {target.name} "
              f"({npaths} paths, {nschemas} schemas, {struct} real conflicts, {repaired} repaired)")
        summary.append(f"{d.name}\t{len(files)}\t{npaths}\t{nschemas}\t{struct}\t{repaired}")
        grand += [f"[{d.name}] {line}" for line in report]
        all_canon.update(cmap)

    (out / "_merge_summary.tsv").write_text(
        "domain\tsources\tpaths\tschemas\tstructural_conflicts\trepaired\n" + "\n".join(summary),
        encoding="utf-8")
    (out / "_merge_report.txt").write_text("\n".join(grand), encoding="utf-8")
    (out / "_conflict_report.json").write_text(
        json.dumps({"total": len(classifier), "conflicts": classifier}, indent=2),
        encoding="utf-8")
    (out / "_canonical_map.json").write_text(json.dumps(all_canon, indent=2), encoding="utf-8")

    by_type = {}
    for c in classifier:
        by_type[c["conflict_type"]] = by_type.get(c["conflict_type"], 0) + 1
    print("\nConflict classification:")
    for k, v in sorted(by_type.items()):
        print(f"   {k}: {v}")
    print(f"\nReports -> {out}\\_merge_summary.tsv, _conflict_report.json, _canonical_map.json")


if __name__ == "__main__":
    main()

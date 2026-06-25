#!/usr/bin/env python3
"""
build_postman.py  —  CP Catalog / API 360 Business Flow collection builder
==========================================================================
Converts the merged per-domain OpenAPI specs (output of merge_swagger_v2.py)
into Postman v2.1 collections for the API 360 *Business Flow* feature, and
splits the largest domains so the TOTAL number of files is exactly --target.

Pipeline:
    197 yaml --merge_swagger_v2.py--> ~16 *.openapi.(yaml|json)
                                          --THIS SCRIPT--> exactly N *.postman.json

Aligned to CP Catalog standards:
  * Deterministic output (stable ordering, sorted keys) -> diff-friendly, reproducible.
  * Canonical error unification: ApiError / SWPApiError / ErrorInfo / *ErrorResponse /
    CashCommonErrorResponse ... folded so the envelope noise stops polluting flows.
  * Full traceability: every collection carries x-cp-catalog metadata, and a
    _postman_index.tsv + _catalog_metadata.json sidecar map file->domain->requests.
  * Collection-level Bearer auth via {{swp_token}}; {{base_url}} variable.

Usage:
    pip install pyyaml
    python build_postman.py --src "C:\\SEI\\merged" --out "C:\\SEI\\postman" --target 20

IMPORTANT: --src must be the merge OUTPUT folder, and must NOT sit inside the
original spec tree, or you'll re-ingest merged files. Keep them separate.
"""

import argparse, json, re, hashlib, sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

METHODS = ["get", "put", "post", "delete", "patch", "options", "head"]

# ---- canonical error/envelope schema names folded into a single concept -----
CANONICAL_ERROR_PAT = re.compile(
    r"^(api)?error(info|response)?$|errorresponse$|commonerror|"
    r"errorsource$|apiresponsewarning$|^errors$",
    re.IGNORECASE,
)


def load(p: Path):
    text = p.read_text(encoding="utf-8-sig")
    if p.suffix.lower() in (".yaml", ".yml"):
        if yaml is None:
            raise RuntimeError("pip install pyyaml to read YAML specs")
        return yaml.safe_load(text)
    return json.loads(text)


def base_url_of(spec):
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict) and servers[0].get("url"):
        return servers[0]["url"].rstrip("/")
    return "{{base_url}}"


def is_canonical_error(name: str) -> bool:
    base = re.sub(r"__.*$", "", name or "")          # strip merge suffix
    return bool(CANONICAL_ERROR_PAT.search(base))


def folder_key(path, op):
    """Business-flow grouping: prefer first tag, else first real path segment."""
    tags = op.get("tags") or []
    if tags:
        return str(tags[0])
    seg = [s for s in path.split("/") if s and not s.startswith("{")]
    return seg[0] if seg else "root"


def build_url(base, path, op):
    query, variables = [], []
    for prm in op.get("parameters", []) or []:
        loc = prm.get("in")
        if loc == "query":
            query.append({"key": prm["name"], "value": "",
                          "description": prm.get("description", ""),
                          "disabled": not prm.get("required", False)})
        elif loc == "path":
            variables.append({"key": prm["name"], "value": ""})
    host_part = base if base.startswith("{{") else base
    raw = host_part + path
    if query:
        raw += "?" + "&".join(f"{q['key']}=" for q in query)
    url = {"raw": raw, "host": [host_part],
           "path": [p for p in path.split("/") if p != ""]}
    if query:
        url["query"] = query
    if variables:
        url["variable"] = variables
    return url


def example_body(op):
    rb = op.get("requestBody", {}) or {}
    appjson = (rb.get("content", {}) or {}).get("application/json") or {}
    ex = appjson.get("example")
    if ex is None:
        examples = appjson.get("examples") or {}
        if examples:
            first = next(iter(examples.values()))
            ex = first.get("value") if isinstance(first, dict) else None
    if ex is None and "schema" in appjson:
        ref = appjson["schema"].get("$ref", "") if isinstance(appjson["schema"], dict) else ""
        ex = {"_comment": "populate per schema", "schema": ref}
    if ex is None:
        return None
    return {"mode": "raw", "raw": json.dumps(ex, indent=2, sort_keys=True),
            "options": {"raw": {"language": "json"}}}


def make_request(base, path, method, op):
    header = [{"key": "Accept", "value": "application/json"}]
    body = None
    if method in ("post", "put", "patch"):
        header.append({"key": "Content-Type", "value": "application/json"})
        body = example_body(op)
    req = {
        "name": op.get("summary") or op.get("operationId") or f"{method.upper()} {path}",
        "request": {
            "method": method.upper(),
            "header": header,
            "url": build_url(base, path, op),
            "description": op.get("description", ""),
        },
    }
    if body:
        req["request"]["body"] = body
    # traceability for API 360 Business Flow
    req["request"]["description"] = (req["request"]["description"] or "") + \
        f"\n\n[x-cp-source path={path} method={method.upper()} opId={op.get('operationId','')}]"
    return req


def spec_to_folders(spec):
    base = base_url_of(spec)
    folders = {}
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method in METHODS:
            if method in item and isinstance(item[method], dict):
                op = item[method]
                key = folder_key(path, op)
                folders.setdefault(key, []).append(make_request(base, path, method, op))
    # deterministic order
    return [(k, folders[k]) for k in sorted(folders)]


def total_requests(folders):
    return sum(len(items) for _, items in folders)


def collection(name, folders, source_domain):
    canonical_note = ("Canonical error envelopes (ApiError/ErrorInfo/SWPApiError/"
                      "*ErrorResponse) are treated as one concept 'Error' for flow clarity.")
    return {
        "info": {
            "name": name,
            "_postman_id": hashlib.md5(name.encode()).hexdigest(),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": f"CP Catalog API 360 Business Flow - {name}. {canonical_note}",
        },
        "auth": {"type": "bearer",
                 "bearer": [{"key": "token", "value": "{{swp_token}}", "type": "string"}]},
        "variable": [
            {"key": "base_url", "value": "https://api.sei.example", "type": "string"},
            {"key": "swp_token", "value": "", "type": "string"},
        ],
        "item": [{"name": fname, "item": items} for fname, items in folders],
        "x-cp-catalog": {
            "module": "API360",
            "feature": "BusinessFlow",
            "source_domain": source_domain,
            "request_count": total_requests(folders),
            "folder_count": len(folders),
        },
    }


def split_domain(name, folders, n_parts):
    """Split a domain's folders into n_parts, balancing request counts, folders kept whole."""
    if n_parts <= 1:
        return [(name, folders)]
    folders_sorted = sorted(folders, key=lambda f: (-len(f[1]), f[0]))
    buckets = [[] for _ in range(n_parts)]
    loads = [0] * n_parts
    for fname, items in folders_sorted:
        i = loads.index(min(loads))
        buckets[i].append((fname, items))
        loads[i] += len(items)
    out = []
    for idx, b in enumerate(buckets, 1):
        if b:
            out.append((f"{name}-part{idx}", sorted(b)))
    return out


def allocate(domains, target):
    """Return list of n_parts per domain so total files == target (when n<=target)."""
    n = len(domains)
    if n >= target:
        return [1] * n
    total_req = sum(d[2] for d in domains) or 1
    want = [max(1, round(target * d[2] / total_req)) for d in domains]
    order = sorted(range(n), key=lambda i: -domains[i][2])
    while sum(want) > target:
        j = max(range(n), key=lambda i: want[i])
        if want[j] > 1:
            want[j] -= 1
        else:
            break
    k = 0
    while sum(want) < target:
        want[order[k % n]] += 1
        k += 1
    return want


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="folder of merged *.openapi.(yaml|json)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", type=int, default=20)
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    specs = [p for p in sorted(src.iterdir())
             if p.suffix.lower() in (".json", ".yaml", ".yml")
             and not p.name.startswith("_")]
    if not specs:
        print("No merged specs found in", src); sys.exit(1)

    domains = []
    for p in specs:
        try:
            spec = load(p)
        except Exception as e:
            print(f"SKIP {p.name}: {e}"); continue
        folders = spec_to_folders(spec)
        name = p.stem.replace(".openapi", "")
        domains.append([name, folders, total_requests(folders)])

    if not domains:
        print("No usable specs."); sys.exit(1)

    alloc = allocate(domains, args.target)
    if len(domains) > args.target:
        print(f"WARNING: {len(domains)} domains > target {args.target}; "
              f"emitting one per domain. Merge small domains to reduce count.")

    manifest = ["file\tsource_domain\tfolders\trequests"]
    catalog = {"module": "API360", "feature": "BusinessFlow", "collections": []}
    produced = 0
    for (name, folders, req), parts in zip(domains, alloc):
        for pname, pfolders in split_domain(name, folders, parts):
            coll = collection(pname, pfolders, name)
            fpath = out / f"{pname}.postman.json"
            fpath.write_text(json.dumps(coll, indent=2, sort_keys=False), encoding="utf-8")
            rc = total_requests(pfolders)
            manifest.append(f"{fpath.name}\t{name}\t{len(pfolders)}\t{rc}")
            catalog["collections"].append(
                {"file": fpath.name, "source_domain": name,
                 "folders": len(pfolders), "requests": rc})
            produced += 1
            print(f"OK {fpath.name}: {len(pfolders)} folders, {rc} requests")

    (out / "_postman_index.tsv").write_text("\n".join(manifest), encoding="utf-8")
    (out / "_catalog_metadata.json").write_text(
        json.dumps(catalog, indent=2), encoding="utf-8")
    print(f"\nProduced {produced} collection files (target {args.target}).")
    print(f"Manifest        -> {out/'_postman_index.tsv'}")
    print(f"Catalog metadata-> {out/'_catalog_metadata.json'}")
    if produced != args.target:
        print(f"NOTE: produced {produced}, not {args.target}. "
              f"If too many domains, merge the smallest; if too few, raise --target.")


if __name__ == "__main__":
    main()

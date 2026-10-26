"""Deterministic repo analysis + Markdown/Mermaid generation (no LLM involved).

Facts in the docs (files, endpoints, models, deps, env vars, CI, diagrams) come from the code itself.
 - Python: deep AST analysis (routes, call flows, models, env vars).
 - Everything else: regex/manifest analysis in generic.py (JS/TS, Java, Go, C#, Ruby, PHP, Rust, SQL,
   Docker, Kubernetes, Terraform, OpenAPI, protobuf, CI ...).
Anything not detectable here is covered by the LLM agent (see agent.py).
Generators return None when a section does not apply to the repo, so it is simply left out of the doc.
"""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import generic as gx

STDLIB = set(getattr(sys, "stdlib_module_names", ()))
HTTP = {"get", "post", "put", "delete", "patch", "head", "options"}
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "dist", "build", "target", "vendor",
             ".idea", ".vscode", ".mypy_cache", ".pytest_cache", ".next", ".gradle", ".terraform", "coverage"}
LOCKFILES = ("package-lock.json", "yarn.lock", "poetry.lock", "pnpm-lock.yaml", "Pipfile.lock", "go.sum",
             "Cargo.lock", "composer.lock", "Gemfile.lock", ".min.js", ".min.css", ".map")
FILE_NOTES = {
    "requirements.txt": "Python dependencies", "package.json": "Node dependencies & scripts",
    "Dockerfile": "Container image", "docker-compose.yml": "Local multi-service setup",
    "README.md": "Project readme", ".gitignore": "Git ignore rules", "Makefile": "Build/automation targets",
    "go.mod": "Go module & dependencies", "pom.xml": "Maven build & dependencies",
    "build.gradle": "Gradle build", "Cargo.toml": "Rust crate manifest", "Gemfile": "Ruby dependencies",
    "composer.json": "PHP dependencies", "pyproject.toml": "Python project config", "tsconfig.json": "TypeScript config",
    ".env.example": "Example environment variables",
}
MAX_ENDPOINTS_IN_DIAGRAM = 25
MAX_FLOWS = 8


# ───────────────────────── file discovery ─────────────────────────
def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def excluded_prefixes():
    raw = os.getenv("DOC_EXCLUDE") or "doc_agent/,run_agent.py,requirements-docs.txt,.github/workflows/docs.yml"
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def is_noise(path):
    """Files that never appear in docs or trigger doc updates."""
    return (path.startswith("docs/") or path.endswith(LOCKFILES)
            or path.startswith(excluded_prefixes())
            or any(p in SKIP_DIRS for p in path.split("/")))


def list_files():
    out = _git("-c", "core.quotepath=off", "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    if not out:
        for dp, dn, fn in os.walk("."):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            out += [os.path.join(dp, f)[2:].replace(os.sep, "/") for f in fn]
    return sorted(f for f in set(out) if not is_noise(f) and Path(f).is_file())


# ───────────────────────── python parsing ─────────────────────────
MODEL_BASES = {"Model", "BaseModel", "Base", "DeclarativeBase", "SQLModel", "Document", "Schema", "TypedDict"}


def _first(doc):
    return doc.strip().splitlines()[0].strip() if doc and doc.strip() else ""


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def _modname(path):
    m = path[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m


def _is_test(path):
    base = path.rsplit("/", 1)[-1]
    return gx.is_test_path(path) or base == "conftest.py" or base.startswith("test_")


def _sig(fn):
    ret = f" -> {ast.unparse(fn.returns)}" if fn.returns else ""
    return f"{fn.name}({ast.unparse(fn.args)}){ret}"


def _raw_calls(fn):
    out = []
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Name):
                out.append((c.lineno, c.col_offset, ("n", f.id)))
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                out.append((c.lineno, c.col_offset, ("a", f.value.id, f.attr)))
    out.sort(key=lambda x: x[:2])
    return [o[2] for o in out]


def _kw(call, *names):
    for k in call.keywords:
        if k.arg in names:
            return _const(k.value)
    return None


def _class_info(n):
    bases = [ast.unparse(b) for b in n.bases]
    decos = [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in n.decorator_list]
    fields = []
    for s in n.body:
        if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name):
            fields.append(s.target.id)
        elif isinstance(s, ast.Assign):
            fields += [t.id for t in s.targets if isinstance(t, ast.Name)]
    fields = [f for f in fields if not f.startswith("_")][:10]
    is_model = (any(b.split(".")[-1] in MODEL_BASES or b.split(".")[-1].endswith("Model") for b in bases)
                or any(d.split(".")[-1] == "dataclass" for d in decos))
    methods = [dict(name=m.name, sig=_sig(m), doc=_first(ast.get_docstring(m)))
               for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return dict(name=n.name, doc=_first(ast.get_docstring(n)), methods=methods, bases=bases, fields=fields, is_model=is_model)


def parse_python(path):
    try:
        tree = ast.parse(Path(path).read_text(errors="ignore"))
    except (SyntaxError, OSError, ValueError):
        return None
    mod = _modname(path)
    pkg = mod if path.endswith("__init__.py") else mod.rpartition(".")[0]
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    info = dict(path=path, name=mod, doc=_first(ast.get_docstring(tree)), empty=not body,
                test=_is_test(path), functions=[], classes=[], routes=[], raw_imports=set(),
                from_imports={}, aliases={}, blueprints={}, registrations=[], envs=[], fn_calls={})

    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                info["raw_imports"].add(a.name)
                top = a.name.split(".")[0]
                info["aliases"][a.asname or top] = a.name if a.asname else top
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".") if pkg else []
                parts = parts[: max(len(parts) - (n.level - 1), 0)]
                base = ".".join(parts + ([n.module] if n.module else []))
            if base:
                info["raw_imports"].add(base)
            for a in n.names:
                info["from_imports"][a.asname or a.name] = (base, a.name)
                info["raw_imports"].add(f"{base}.{a.name}" if base else a.name)
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            f = n.value.func
            fname = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if fname in ("Blueprint", "APIRouter"):
                prefix = _kw(n.value, "url_prefix", "prefix") or ""
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        info["blueprints"][t.id] = prefix
        elif isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in ("register_blueprint", "include_router") and n.args:
                prefix, a0 = _kw(n, "url_prefix", "prefix"), n.args[0]
                if prefix is not None:
                    if isinstance(a0, ast.Name):
                        info["registrations"].append((("n", a0.id), prefix))
                    elif isinstance(a0, ast.Attribute) and isinstance(a0.value, ast.Name):
                        info["registrations"].append((("a", a0.value.id, a0.attr), prefix))
            if isinstance(f, ast.Attribute) and n.args and isinstance(_const(n.args[0]), str):
                is_getenv = f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os"
                is_environ_get = f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
                if is_getenv or is_environ_get:
                    default = ast.unparse(n.args[1]) if len(n.args) > 1 else "—"
                    info["envs"].append((_const(n.args[0]), default))
        elif isinstance(n, ast.Subscript):
            v = n.value
            if isinstance(v, ast.Attribute) and v.attr == "environ" and isinstance(_const(n.slice), str):
                info["envs"].append((_const(n.slice), "(required)"))

    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["fn_calls"][n.name] = _raw_calls(n)
            for dec in n.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and (dec.func.attr == "route" or dec.func.attr in HTTP)):
                    path_arg = _const(dec.args[0]) if dec.args else _kw(dec, "path", "rule")
                    methods = [dec.func.attr.upper()]
                    if dec.func.attr == "route":
                        methods = ["GET"]
                        for k in dec.keywords:
                            if k.arg == "methods" and isinstance(k.value, (ast.List, ast.Tuple)):
                                methods = [_const(e).upper() for e in k.value.elts if isinstance(_const(e), str)] or methods
                    info["routes"].append(dict(obj=dec.func.value.id, methods=methods, path=path_arg or "/",
                                               handler=n.name, doc=_first(ast.get_docstring(n)), line=n.lineno))

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info["functions"].append(dict(name=n.name, sig=_sig(n), doc=_first(ast.get_docstring(n))))
        elif isinstance(n, ast.ClassDef):
            info["classes"].append(_class_info(n))
    return info


# ───────────────────────── dependencies ─────────────────────────
def _parse_req(line, source):
    line = line.split("#")[0].strip()
    if not line or line.startswith("-"):
        return None
    m = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)", line)
    return (m.group(1), m.group(2).strip() or "any", source) if m else None


def read_deps(files):
    deps = []
    for f in files:
        n = Path(f).name
        t = gx.text(f)
        if n == "requirements.txt":
            deps += [d for d in (_parse_req(l, f) for l in t.splitlines()) if d]
        elif n == "package.json" and "/" not in f:
            try:
                pj = json.loads(t)
                for sect in ("dependencies", "devDependencies"):
                    deps += [(k, v, f"{f} ({sect})") for k, v in pj.get(sect, {}).items()]
            except ValueError:
                pass
        elif n == "pyproject.toml":
            try:
                import tomllib
                for l in tomllib.loads(t).get("project", {}).get("dependencies", []):
                    d = _parse_req(l, f)
                    if d:
                        deps.append(d)
            except Exception:
                pass
    deps += gx.scan_manifests(files)
    return sorted(set(deps), key=lambda d: (d[2], d[0].lower()))


# ───────────────────────── whole-repo analysis ─────────────────────────
def _join(prefix, path):
    full = re.sub(r"/+", "/", "/" + (prefix or "").strip("/") + "/" + (path or "").strip("/"))
    return full.rstrip("/") or "/"


def _pretty(path):
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", path)


def analyze():
    gx.clear_cache()
    files = list_files()
    mods = {}
    for f in files:
        if f.endswith(".py"):
            m = parse_python(f)
            if m:
                mods[m["name"]] = m
    internal = set(mods)

    def resolve_ref(m, ref):
        if ref[0] == "n":
            fi = m["from_imports"].get(ref[1])
            return (fi[0], fi[1]) if fi else (m["name"], ref[1])
        alias, attr = ref[1], ref[2]
        fi = m["from_imports"].get(alias)
        if fi:
            return (f"{fi[0]}.{fi[1]}", attr)
        if alias in m["aliases"]:
            return (m["aliases"][alias], attr)
        return (None, attr)

    registered = {}
    for m in mods.values():
        for ref, prefix in m["registrations"]:
            registered[resolve_ref(m, ref)] = prefix

    routes = []
    for m in mods.values():
        if m["test"]:
            continue
        for r in m["routes"]:
            prefix = registered.get((m["name"], r["obj"]), m["blueprints"].get(r["obj"], ""))
            for meth in r["methods"]:
                routes.append(dict(method=meth, path=_pretty(_join(prefix, r["path"])), handler=r["handler"],
                                   doc=r["doc"], file=m["path"], line=r["line"], module=m["name"]))
    routes += gx.scan_routes(files)
    routes.sort(key=lambda r: (r["path"], r["method"]))

    shown = sorted((m for m in mods.values() if not m["test"] and not m["empty"]), key=lambda m: m["path"])
    shown_names = {m["name"] for m in shown}
    tops = {n.split(".")[0] for n in internal}
    ext = {}
    for m in shown:
        libs = {i.split(".")[0] for i in m["raw_imports"]}
        ext[m["name"]] = sorted(l for l in libs if l and l not in tops and l not in STDLIB)

    envs = {}
    for m in mods.values():
        if not m["test"]:
            for name, default in m["envs"]:
                envs.setdefault(name, (default, set()))[1].add(m["path"])
    for name, (default, where) in gx.scan_envs(files).items():
        if name in envs:
            envs[name][1].update(where)
        else:
            envs[name] = (default, set(where))

    models = []
    for m in shown:
        for c in m["classes"]:
            if c["is_model"]:
                models.append(dict(name=c["name"], kind="Python " + ("model" if c["bases"] else "dataclass"),
                                   file=m["path"], fields=c["fields"]))
    models += gx.scan_models(files)

    edges = gx.import_edges(files)
    return dict(files=files, mods=mods, internal=internal, shown=shown, shown_names=shown_names,
                routes=routes, ext=ext, envs=envs, deps=read_deps(files), models=models,
                langs=gx.language_stats(files), symbols=gx.scan_symbols(files), tests=gx.scan_tests(files),
                components=gx.components(files, edges), dockerfiles=gx.scan_dockerfiles(files),
                compose=gx.scan_compose(files), workflows=gx.scan_workflows(files), infra=gx.scan_infra(files),
                commands=gx.scan_commands(files))


# ───────────────────────── markdown helpers ─────────────────────────
def _cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def _mm(s):
    return str(s).replace('"', "'").replace(";", ",").replace("#", "").replace("|", "/")


def _id(prefix, name):
    return prefix + re.sub(r"\W", "_", name)


def _fence(body):
    return "```mermaid\n" + body + "\n```"


# ───────────────────────── section generators ─────────────────────────
def md_glance(a):
    nfun = sum(len(m["functions"]) for m in a["shown"])
    tests = [m for m in a["mods"].values() if m["test"] and not m["empty"]]
    ext = sorted({l for v in a["ext"].values() for l in v})
    langs = ", ".join(f"{l} ({n} files, {ln:,} lines)" for l, n, ln in a["langs"][:6]) or "—"
    rows = [f"- **Files:** {len(a['files'])}", f"- **Languages:** {langs}"]
    if a["shown"]:
        rows.append(f"- **Python modules:** {len(a['shown'])} ({nfun} top-level functions)")
    rows += [f"- **HTTP/RPC endpoints:** {len(a['routes'])}",
             f"- **Data models:** {len(a['models'])}",
             f"- **Test files:** {len(tests) + len(a['tests'])}",
             f"- **Dependencies declared:** {len(a['deps'])}",
             f"- **Environment variables:** {len(a['envs'])}"]
    if ext:
        rows.append(f"- **Third-party Python libraries imported:** {', '.join(f'`{l}`' for l in ext)}")
    return "\n".join(rows)


def _python_diagram(a):
    shown = a["shown"]
    by_mod = {}
    for r in a["routes"]:
        if r["module"]:
            by_mod.setdefault(r["module"], []).append(r)
    ids = {m["name"]: _id("m_", m["name"]) for m in shown}
    L = ["flowchart LR"]
    if by_mod:
        L.append("  client([Client])")
    ep_lines, total = [], 0
    for m in shown:
        rs = by_mod.get(m["name"], [])
        if rs:
            L.append(f'  subgraph {ids[m["name"]]}["{_mm(m["path"])}"]')
            for i, r in enumerate(rs):
                if total >= MAX_ENDPOINTS_IN_DIAGRAM:
                    break
                nid = f'{ids[m["name"]]}_e{i}'
                L.append(f'    {nid}["{r["method"]} {_mm(r["path"])}"]')
                ep_lines.append(f"  client --> {nid}")
                total += 1
            L.append("  end")
        else:
            L.append(f'  {ids[m["name"]]}["{_mm(m["path"])}"]')
    L += ep_lines
    for m in shown:
        for t in sorted(i for i in m["raw_imports"] if i in a["shown_names"] and i != m["name"]):
            L.append(f"  {ids[m['name']]} --> {ids[t]}")
    libs = sorted({l for v in a["ext"].values() for l in v})
    for l in libs:
        L.append(f'  {_id("ext_", l)}(["{_mm(l)}"])')
    for m in shown:
        for l in a["ext"][m["name"]]:
            L.append(f"  {ids[m['name']]} -.-> {_id('ext_', l)}")
    return _fence("\n".join(L))


def _component_diagram(a):
    comp = a["components"]
    if not comp:
        return None
    nodes, edges, prefix = comp
    if len(nodes) < 2 and not edges:
        return None

    def label(c):
        rel = c[len(prefix):].strip("/") if prefix and c.startswith(prefix) else c
        return rel or (prefix.rsplit("/", 1)[-1] if prefix else c)

    L = ["flowchart LR"]
    for c, (n, lang) in sorted(nodes.items()):
        L.append(f'  {_id("c_", c)}["{_mm(label(c))}/ · {n} files · {_mm(lang)}"]')
    for (x, y), n in sorted(edges.items()):
        L.append(f'  {_id("c_", x)} -->{"|" + str(n) + "|" if n > 1 else ""} {_id("c_", y)}')
    note = f"_Folders are relative to `{prefix}/`._\n\n" if prefix else ""
    return note + _fence("\n".join(L))


def _compose_diagram(a):
    blocks = []
    for comp in a["compose"]:
        svcs = comp["services"]
        if not svcs:
            continue
        L = ["flowchart LR", "  user([User])"]
        for name, s in svcs.items():
            port = f' · :{_mm(s["ports"][0].split(":")[0])}' if s["ports"] else ""
            L.append(f'  {_id("s_", name)}["{_mm(name)} · {_mm(s["image"])}{port}"]')
        for name, s in svcs.items():
            if s["ports"]:
                L.append(f'  user --> {_id("s_", name)}')
            for d in s["depends_on"]:
                if d in svcs:
                    L.append(f'  {_id("s_", name)} --> {_id("s_", d)}')
        blocks.append((comp["file"], _fence("\n".join(L))))
    return blocks


def _folder_diagram(a):
    top = {}
    for f in a["files"]:
        top.setdefault(f.split("/")[0] if "/" in f else "(root files)", []).append(f)
    if len(top) < 2:
        return None
    L = ["flowchart LR", '  repo(["repository"])']
    for i, (d, fs) in enumerate(sorted(top.items())):
        L.append(f'  repo --> d{i}["{_mm(d)} ({len(fs)} files)"]')
    return _fence("\n".join(L))


def md_architecture(a):
    parts = []
    if a["shown"]:
        parts.append(("Python modules and endpoints", _python_diagram(a)))
    cd = _component_diagram(a)
    if cd:
        parts.append(("Code components (import dependencies between folders)", cd))
    for f, d in _compose_diagram(a):
        parts.append((f"Runtime topology (`{f}`)", d))
    if not parts:
        return _folder_diagram(a)
    if len(parts) == 1:
        return parts[0][1]
    return "\n\n".join(f"#### {t}\n\n{d}" for t, d in parts)


def _resolve_calls(a, m, calls):
    out = []
    for c in calls:
        tgt = None
        if c[0] == "n":
            fi = m["from_imports"].get(c[1])
            if fi and fi[0] in a["internal"]:
                tgt = (fi[0], fi[1])
        else:
            fi = m["from_imports"].get(c[1])
            if fi and f"{fi[0]}.{fi[1]}" in a["internal"]:
                tgt = (f"{fi[0]}.{fi[1]}", c[2])
            elif c[1] in m["aliases"] and m["aliases"][c[1]] in a["internal"]:
                tgt = (m["aliases"][c[1]], c[2])
        if tgt and tgt[0] != m["name"] and tgt[0] in a["shown_names"] and tgt not in out:
            out.append(tgt)
    return out


def _flow(a, m, fn, caller, depth, lines, parts, seen):
    for tm, tf in _resolve_calls(a, m, m["fn_calls"].get(fn, [])):
        tid = _id("p_", tm)
        parts[tid] = a["mods"][tm]["path"]
        lines.append(f"  {caller}->>{tid}: {_mm(tf)}()")
        if depth < 2 and (tm, tf) not in seen and tf in a["mods"][tm]["fn_calls"]:
            seen.add((tm, tf))
            _flow(a, a["mods"][tm], tf, tid, depth + 1, lines, parts, seen)
        lines.append(f"  {tid}-->>{caller}: result")


def md_flows(a):
    blocks = []
    for r in a["routes"]:
        if len(blocks) >= MAX_FLOWS:
            break
        m = a["mods"].get(r["module"]) if r["module"] else None
        if not m:
            continue
        cid, lines, parts = _id("p_", m["name"]), [], {}
        parts[cid] = m["path"]
        _flow(a, m, r["handler"], cid, 1, lines, parts, set())
        if not lines:
            continue
        seq = ["sequenceDiagram", "  autonumber", "  actor Client"]
        seq += [f"  participant {k} as {_mm(v)}" for k, v in parts.items()]
        seq.append(f"  Client->>{cid}: {r['method']} {_mm(r['path'])}")
        seq += lines
        seq.append(f"  {cid}-->>Client: response")
        blocks.append(f"#### `{r['method']} {r['path']}`\n\nHandler: `{r['handler']}` in `{r['file']}`\n\n" + _fence("\n".join(seq)))
    return "\n\n".join(blocks) or None


def md_structure(a):
    desc = {m["path"]: m["doc"] for m in a["mods"].values() if m["doc"]}
    for f in a["files"]:
        desc.setdefault(f, FILE_NOTES.get(f.rsplit("/", 1)[-1], ""))
    tree = {}
    for p in a["files"]:
        cur = tree
        parts = p.split("/")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = None
    root = os.getenv("GITHUB_REPOSITORY", "").split("/")[-1] or Path.cwd().name
    lines = [root + "/"]
    budget = [int(os.getenv("DOC_TREE_MAX_LINES", "400"))]

    def walk(node, prefix, path):
        items = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0]))
        for i, (name, child) in enumerate(items):
            if budget[0] <= 0:
                return
            budget[0] -= 1
            last = i == len(items) - 1
            full = f"{path}{name}"
            note = f"  # {desc[full]}" if child is None and desc.get(full) else ""
            lines.append(f"{prefix}{'└── ' if last else '├── '}{name}{'/' if child is not None else ''}{note}")
            if child is not None:
                walk(child, prefix + ("    " if last else "│   "), full + "/")

    walk(tree, "", "")
    if budget[0] <= 0:
        lines.append("… (truncated: very large repo)")
    return "```text\n" + "\n".join(lines) + "\n```"


def md_api(a):
    if not a["routes"]:
        return None
    rows = ["| Method | Path | Handler | Location | Description |", "|---|---|---|---|---|"]
    for r in a["routes"]:
        loc = f"{r['file']}:{r['line']}" if r["line"] else r["file"]
        rows.append(f"| `{r['method']}` | `{_cell(r['path'])}` | `{_cell(r['handler'])}` | `{loc}` | {_cell(r['doc'])} |")
    return "\n".join(rows)


def md_data(a):
    if not a["models"]:
        return None
    rows = ["| Name | Kind | Fields / columns | Defined in |", "|---|---|---|---|"]
    for m in sorted(a["models"], key=lambda m: (m["file"], m["name"])):
        rows.append(f"| `{m['name']}` | {m['kind']} | {_cell(', '.join(m['fields'])) or '—'} | `{m['file']}` |")
    return "\n".join(rows)


def md_modules(a):
    out = []
    for m in a["shown"]:
        rows = []
        for f in m["functions"]:
            if not f["name"].startswith("_"):
                rows.append(f"| `{_cell(f['sig'])}` | function | {_cell(f['doc'])} |")
        for c in m["classes"]:
            rows.append(f"| `class {c['name']}` | class | {_cell(c['doc'])} |")
            for f in c["methods"]:
                if not f["name"].startswith("_"):
                    rows.append(f"| `{c['name']}.{_cell(f['sig'])}` | method | {_cell(f['doc'])} |")
        block = f"### `{m['path']}`\n\n{m['doc']}\n" if m["doc"] else f"### `{m['path']}`\n"
        if rows:
            block += "\n| Signature | Kind | Description |\n|---|---|---|\n" + "\n".join(rows)
        out.append(block)
    if a["symbols"]:
        rows = ["| File | Symbols |", "|---|---|"]
        items = sorted(a["symbols"].items())
        for f, syms in items[:80]:
            rows.append(f"| `{f}` | {_cell(', '.join(f'{n} ({k})' for k, n, _ in syms))} |")
        if len(items) > 80:
            rows.append(f"| … | +{len(items) - 80} more files |")
        out.append("### Other languages (detected symbols)\n\n" + "\n".join(rows))
    return "\n\n".join(out) or None


def md_deps(a):
    if not a["deps"]:
        return None
    rows = ["| Package | Version | Source |", "|---|---|---|"]
    rows += [f"| `{_cell(n)}` | `{_cell(v)}` | {_cell(s)} |" for n, v, s in a["deps"][:150]]
    if len(a["deps"]) > 150:
        rows.append(f"| … | | +{len(a['deps']) - 150} more |")
    return "\n".join(rows)


def md_env(a):
    if not a["envs"]:
        return None
    rows = ["| Variable | Default | Used in |", "|---|---|---|"]
    for name in sorted(a["envs"]):
        default, where = a["envs"][name]
        rows.append(f"| `{name}` | `{_cell(default)}` | {', '.join(f'`{w}`' for w in sorted(where)[:4])} |")
    return "\n".join(rows)


def md_tests(a):
    rows = []
    for m in sorted((m for m in a["mods"].values() if m["test"] and not m["empty"]), key=lambda m: m["path"]):
        names = [f["name"] for f in m["functions"] if f["name"].startswith("test")]
        for c in m["classes"]:
            names += [f"{c['name']}.{f['name']}" for f in c["methods"] if f["name"].startswith("test")]
        more = f", … (+{len(names) - 6})" if len(names) > 6 else ""
        rows.append(f"| `{m['path']}` | {len(names)} | {', '.join(f'`{n}`' for n in names[:6])}{more} |")
    for f, n in sorted(a["tests"]):
        rows.append(f"| `{f}` | {n} | |")
    if not rows:
        return None
    return "| File | # tests | Test functions |\n|---|---|---|\n" + "\n".join(rows)


def md_deploy(a):
    out = []
    if a["dockerfiles"]:
        rows = ["| Dockerfile | Base image(s) | Exposes | Start command |", "|---|---|---|---|"]
        rows += [f"| `{d['file']}` | {_cell(', '.join(d['bases']) or '—')} | {_cell(d['ports'] or '—')} | `{_cell(d['cmd'])}` |" for d in a["dockerfiles"]]
        out.append("### Containers\n\n" + "\n".join(rows))
    for c in a["compose"]:
        if c["services"]:
            rows = ["| Service | Image / build | Ports | Depends on |", "|---|---|---|---|"]
            rows += [f"| `{n}` | {_cell(s['image'])} | {_cell(', '.join(s['ports']) or '—')} | {_cell(', '.join(s['depends_on']) or '—')} |" for n, s in c["services"].items()]
            out.append(f"### Compose services (`{c['file']}`)\n\n" + "\n".join(rows))
    if a["workflows"]:
        rows = ["| Pipeline | Triggers | Jobs | File |", "|---|---|---|---|"]
        rows += [f"| {_cell(w['name'])} | {_cell(', '.join(w['triggers']) or '—')} | {_cell(', '.join(w['jobs']) or '—')} | `{w['file']}` |" for w in a["workflows"]]
        out.append("### CI/CD pipelines\n\n" + "\n".join(rows))
    if a["infra"]:
        rows = ["| Type | Kind | Name | File |", "|---|---|---|---|"]
        rows += [f"| {t} | `{_cell(k)}` | `{_cell(n)}` | `{f}` |" for t, k, n, f in a["infra"]]
        out.append("### Infrastructure as code\n\n" + "\n".join(rows))
    return "\n\n".join(out) or None


def md_commands(a):
    if not a["commands"]:
        return None
    rows = ["| Command | Runs | Defined in |", "|---|---|---|"]
    rows += [f"| `{_cell(c)}` | {('`' + _cell(r) + '`') if r else ''} | `{f}` |" for c, r, f in a["commands"]]
    return "\n".join(rows)


def md_changelog():
    out = _git("log", "-n", "40", "--date=short", "--pretty=format:%h|%ad|%an|%s", "--", ".", ":(exclude)docs")
    rows = ["| Commit | Date | Author | Message |", "|---|---|---|---|"]
    n = 0
    for line in out.splitlines():
        h, d, who, msg = (line.split("|", 3) + ["", "", "", ""])[:4]
        if msg.startswith("docs: auto-update"):
            continue
        rows.append(f"| `{h}` | {d} | {_cell(who)} | {_cell(msg)} |")
        n += 1
        if n >= 15:
            break
    return "\n".join(rows) if n else "_No commits yet._"


def build_sections(a, changelog=False):
    s = dict(glance=md_glance(a), architecture=md_architecture(a), flows=md_flows(a),
             structure=md_structure(a), api=md_api(a), data=md_data(a), modules=md_modules(a),
             dependencies=md_deps(a), env=md_env(a), deployment=md_deploy(a), commands=md_commands(a),
             tests=md_tests(a))
    if changelog:
        s["changelog"] = md_changelog()
    return s

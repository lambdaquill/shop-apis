"""Language-agnostic extraction so the docs work for ANY repo.

Everything here is regex/manifest based, deterministic and best-effort. Python gets deeper AST analysis
in analyzer.py; this module covers JS/TS, Java/Kotlin, Go, C#, Ruby, PHP, Rust, C/C++, SQL, Docker,
Kubernetes, Terraform, CI, OpenAPI, protobuf and more. Anything it cannot see is handled by the LLM agent.
"""
import json
import posixpath
import re
from collections import Counter, defaultdict
from pathlib import Path

MAX_READ = 400_000
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".jar", ".war",
              ".class", ".exe", ".dll", ".so", ".dylib", ".woff", ".woff2", ".ttf", ".eot", ".mp3", ".mp4",
              ".mov", ".pyc", ".db", ".sqlite", ".lock", ".bin", ".wasm", ".svg"}
LANG = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
        ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue", ".svelte": "Svelte", ".java": "Java",
        ".kt": "Kotlin", ".scala": "Scala", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
        ".cs": "C#", ".cpp": "C++", ".cc": "C++", ".c": "C", ".h": "C/C++", ".hpp": "C++", ".swift": "Swift",
        ".dart": "Dart", ".lua": "Lua", ".sh": "Shell", ".sql": "SQL", ".tf": "Terraform", ".yml": "YAML",
        ".yaml": "YAML", ".json": "JSON", ".md": "Markdown", ".html": "HTML", ".css": "CSS", ".scss": "CSS",
        ".proto": "Protobuf", ".graphql": "GraphQL", ".gql": "GraphQL", ".ipynb": "Jupyter", ".r": "R"}
NON_CODE = {"YAML", "JSON", "Markdown", "HTML", "CSS", "SQL", "Terraform", "Jupyter", "Protobuf", "GraphQL", "Shell"}
JS_EXT = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte"}
Q = "['\"]"
NAME = r"([A-Za-z_]\w*)"
TEST_RX = re.compile(
    r"((^|/)(tests?|__tests__|spec|specs|e2e|cypress)/)|"
    r"((_test\.(go|py|rb|rs)|\.(test|spec)\.[jt]sx?|Tests?\.(java|kt|cs|swift)|(^|/)test_[^/]*\.py|_spec\.rb)$)")

_cache = {}


def clear_cache():
    _cache.clear()


def text(path):
    if path in _cache:
        return _cache[path]
    t = ""
    p = Path(path)
    try:
        if p.suffix.lower() not in BINARY_EXT and p.stat().st_size <= MAX_READ:
            raw = p.read_bytes()
            if b"\0" not in raw[:2048]:
                t = raw.decode("utf-8", "ignore")
    except OSError:
        pass
    _cache[path] = t
    return t


def is_test_path(f):
    return bool(TEST_RX.search(f))


def lang_of(f):
    n = Path(f).name
    if n == "Dockerfile" or n.startswith("Dockerfile.") or n.endswith(".Dockerfile"):
        return "Dockerfile"
    return LANG.get(Path(f).suffix.lower())


def _line(t, pos):
    return t.count("\n", 0, pos) + 1


def _route(method, path, handler, file, line, doc=""):
    return dict(method=method, path=path, handler=handler or "—", doc=doc, file=file, line=line, module=None)


# ───────────────────────── languages ─────────────────────────
def language_stats(files):
    lines, count = Counter(), Counter()
    for f in files:
        lang = lang_of(f)
        if not lang:
            continue
        t = text(f)
        count[lang] += 1
        lines[lang] += t.count("\n") + (1 if t else 0)
    return [(l, count[l], lines[l]) for l, _ in lines.most_common()]


# ───────────────────────── env vars ─────────────────────────
ENV_RX = [re.compile(p) for p in [
    r"process\.env\.([A-Za-z_]\w*)",
    r"process\.env\[\s*" + Q + NAME + Q,
    r"import\.meta\.env\.([A-Za-z_]\w*)",
    r"os\.(?:Getenv|LookupEnv)\(\s*" + Q + NAME + Q,
    r"System\.getenv\(\s*" + Q + NAME + Q,
    r"\bENV\[\s*" + Q + NAME + Q,
    r"\bENV\.fetch\(\s*" + Q + NAME + Q,
    r"env::var\(\s*" + Q + NAME + Q,
    r"\bgetenv\(\s*" + Q + NAME + Q,
    r"\$_ENV\[\s*" + Q + NAME + Q,
    r"GetEnvironmentVariable\(\s*" + Q + NAME + Q,
    r"\benv\(\s*" + Q + r"([A-Z][A-Z0-9_]*)" + Q,
    r"@Value\(\s*" + Q + r"\$\{([A-Za-z0-9_.\-]+)",
]]
ENV_EXAMPLE = {".env.example", ".env.sample", ".env.template", "example.env", "sample.env", ".env.dist"}
COMPOSE_VAR = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::?-([^}]*))?\}")


def scan_envs(files):
    envs = {}

    def add(name, default, where):
        envs.setdefault(name, [default, set()])
        if envs[name][0] in ("—", None) and default not in ("—", None):
            envs[name][0] = default
        envs[name][1].add(where)

    for f in files:
        name, ext = Path(f).name, Path(f).suffix.lower()
        t = text(f)
        if not t:
            continue
        if name in ENV_EXAMPLE:
            for line in t.splitlines():
                m = re.match(r"\s*(?:export\s+)?([A-Za-z_]\w*)\s*=\s*(.*)", line)
                if m and not line.lstrip().startswith("#"):
                    add(m.group(1), m.group(2).strip().strip("\"'") or "—", f)
            continue
        if lang_of(f) in ("YAML", "Dockerfile") or name.startswith("docker-compose"):
            for m in COMPOSE_VAR.finditer(t):
                add(m.group(1), m.group(2) if m.group(2) is not None else "(required)", f)
            continue
        if ext == ".py" or is_test_path(f) or lang_of(f) in (None, "Markdown", "JSON", "Jupyter"):
            continue
        for rx in ENV_RX:
            for m in rx.finditer(t):
                add(m.group(1), "—", f)
    return {k: (v[0], v[1]) for k, v in envs.items()}


# ───────────────────────── routes ─────────────────────────
def _next_method(t, pos):
    seg = re.sub(r"@\w+(?:\([^)]*\))?", " ", t[pos:pos + 500])
    m = re.search(r"([A-Za-z_]\w*)\s*\(", seg)
    return m.group(1) if m else None


def _join(prefix, path):
    full = re.sub(r"/+", "/", "/" + (prefix or "").strip("/") + "/" + (path or "").strip("/"))
    return full.rstrip("/") or "/"


def _pretty(p):
    return re.sub(r"<(?:[^:>]+:)?([^>]+)>|:(\w+)(?=/|$)", lambda m: "{" + (m.group(1) or m.group(2)) + "}", p)


JS_RECV = r"\b\w*(?:app|App|router|Router|server|Server|api|Api|routes|Routes|fastify|route)"
QT = "['\"`]"
JS_ROUTE = re.compile(JS_RECV + r"\.(get|post|put|delete|patch|all|head|options)\(\s*" + QT + r"([^'\"`]*)" + QT)
GO_ROUTE = re.compile(r"\b\w+\.(GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch|HandleFunc|Handle)\(\s*\"(/[^\"]*)\"(?:\s*,\s*([\w.]+))?")
GO_HTTP = re.compile(r"\bhttp\.(?:HandleFunc|Handle)\(\s*\"(/[^\"]*)\"(?:\s*,\s*([\w.]+))?")


def _openapi(f, t):
    out = []
    try:
        if f.lower().endswith(".json"):
            spec = json.loads(t)
        else:
            import yaml
            spec = yaml.safe_load(t)
        for path, item in (spec.get("paths") or {}).items():
            for meth, op in (item or {}).items():
                if meth.lower() in ("get", "post", "put", "delete", "patch", "head", "options") and isinstance(op, dict):
                    doc = (op.get("summary") or op.get("description") or "").strip().splitlines()
                    out.append(_route(meth.upper(), _pretty(path), op.get("operationId"), f, None, doc[0] if doc else ""))
    except Exception:
        pass
    return out


def _proto(f, t):
    out, svc = [], None
    for i, line in enumerate(t.splitlines(), 1):
        m = re.match(r"\s*service\s+(\w+)", line)
        if m:
            svc = m.group(1)
        m = re.match(r"\s*rpc\s+(\w+)\s*\(\s*(?:stream\s+)?([\w.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?([\w.]+)", line)
        if m and svc:
            out.append(_route("RPC", f"{svc}/{m.group(1)}", f"{m.group(2)} → {m.group(3)}", f, i))
    return out


def scan_routes(files):
    out = []
    django_includes = {}
    for f in files:
        if is_test_path(f):
            continue
        name, ext, low = Path(f).name, Path(f).suffix.lower(), f.lower()
        t = text(f)
        if not t:
            continue
        if name.lower() in ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json", "swagger.yaml", "swagger.yml"):
            out += _openapi(f, t)
        if ext == ".proto":
            out += _proto(f, t)
        if ext in JS_EXT:
            m = re.search(r"@Controller\(\s*" + Q + r"([^'\"]*)" + Q, t)
            prefix = m.group(1) if m else ""
            for m in re.finditer(r"@(Get|Post|Put|Delete|Patch|All)\(\s*(?:" + Q + r"([^'\"]*)" + Q + r")?\s*\)", t):
                out.append(_route(m.group(1).upper(), _pretty(_join(prefix, m.group(2) or "")),
                                  _next_method(t, m.end()), f, _line(t, m.start())))
            for m in JS_ROUTE.finditer(t):
                if m.group(2).startswith("/"):
                    out.append(_route(m.group(1).upper(), _pretty(m.group(2)), "—", f, _line(t, m.start())))
            mp = re.search(r"(?:^|/)pages/api/(.+)\.(?:js|ts|jsx|tsx)$", f)
            if mp:
                p = re.sub(r"/?index$", "", mp.group(1))
                out.append(_route("ANY", _pretty("/api/" + re.sub(r"\[(?:\.\.\.)?(\w+)\]", r"{\1}", p)), "default export", f, 1))
            mp = re.search(r"(?:^|/)app/(.*)route\.(?:js|ts)$", f)
            if mp:
                p = re.sub(r"\([^)/]*\)/?", "", mp.group(1)).strip("/")
                p = re.sub(r"\[(?:\.\.\.)?(\w+)\]", r"{\1}", p)
                for m in re.finditer(r"export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|DELETE|PATCH)\b", t):
                    out.append(_route(m.group(1), "/" + p, m.group(1), f, _line(t, m.start())))
        elif ext in (".java", ".kt", ".scala"):
            cls = re.search(r"\b(?:class|object)\s+\w+", t)
            head = t[:cls.start()] if cls else t
            m = re.search(r"@RequestMapping\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*\"([^\"]*)\"", head)
            prefix = m.group(1) if m else ""
            start = cls.start() if cls else 0
            for m in re.finditer(r"@(Get|Post|Put|Delete|Patch)Mapping(?:\(\s*(?:(?:value|path)\s*=\s*)?\{?\s*\"([^\"]*)\")?", t[start:]):
                pos = start + m.end()
                out.append(_route(m.group(1).upper(), _pretty(_join(prefix, m.group(2) or "")), _next_method(t, pos),
                                  f, _line(t, start + m.start())))
            for m in re.finditer(r"@RequestMapping\(([^)]*)\)", t[start:]):
                args = m.group(1)
                pm = re.search(r"\"([^\"]*)\"", args)
                mm = re.search(r"RequestMethod\.(\w+)", args)
                out.append(_route(mm.group(1) if mm else "ANY", _pretty(_join(prefix, pm.group(1) if pm else "")),
                                  _next_method(t, start + m.end()), f, _line(t, start + m.start())))
        elif ext == ".cs":
            cls = re.search(r"\bclass\s+(\w+?)(?:Controller)?\b", t)
            m = re.search(r"\[Route\(\s*\"([^\"]*)\"\s*\)\]", t[:cls.start()] if cls else t)
            prefix = (m.group(1) if m else "").replace("[controller]", (cls.group(1) if cls else "").lower())
            for m in re.finditer(r"\[Http(Get|Post|Put|Delete|Patch)(?:\(\s*\"([^\"]*)\"\s*\))?\]", t):
                out.append(_route(m.group(1).upper(), _pretty(_join(prefix, m.group(2) or "")),
                                  _next_method(t, m.end()), f, _line(t, m.start())))
        elif ext == ".go":
            for m in GO_HTTP.finditer(t):
                out.append(_route("ANY", m.group(1), m.group(2), f, _line(t, m.start())))
            for m in GO_ROUTE.finditer(t):
                if m.group(1) in ("HandleFunc", "Handle") and "http." in t[max(0, m.start() - 6):m.start() + 6]:
                    continue
                meth = "ANY" if m.group(1) in ("Handle", "HandleFunc") else m.group(1).upper()
                out.append(_route(meth, _pretty(m.group(2)), m.group(3), f, _line(t, m.start())))
        elif ext == ".rb" and low.endswith("routes.rb"):
            for m in re.finditer(r"^\s*(get|post|put|patch|delete)\s+" + Q + r"([^'\"]+)" + Q, t, re.M):
                out.append(_route(m.group(1).upper(), _pretty("/" + m.group(2).lstrip("/")), "—", f, _line(t, m.start())))
            for m in re.finditer(r"^\s*resources?\s+:(\w+)", t, re.M):
                out.append(_route("REST", "/" + m.group(1), "resources", f, _line(t, m.start())))
        elif ext == ".php":
            for m in re.finditer(r"Route::(get|post|put|delete|patch|any)\(\s*" + Q + r"([^'\"]*)" + Q + r"\s*,\s*([^)]{0,60})", t):
                out.append(_route(m.group(1).upper(), _pretty("/" + m.group(2).lstrip("/")), m.group(3).strip(), f, _line(t, m.start())))
        elif ext == ".py" and name == "urls.py":
            for m in re.finditer(r"\b(?:re_)?path\(\s*" + Q + r"([^'\"]*)" + Q + r"\s*,\s*(include\(\s*" + Q + r"([\w.]+)" + Q + r"|[\w.]+)", t):
                pth = "/" + m.group(1).lstrip("^").rstrip("$").lstrip("/")
                if m.group(3):
                    django_includes[m.group(3)] = pth
                else:
                    out.append(_route("ANY", _pretty(pth), m.group(2), f, _line(t, m.start())))
    for r in out:  # apply include() prefixes, e.g. blog/urls.py mounted at /blog/
        if r["file"].endswith("urls.py"):
            mod = r["file"][:-3].replace("/", ".")
            for inc, prefix in django_includes.items():
                if mod == inc or mod.endswith("." + inc) or inc.endswith("." + mod):
                    r["path"] = _pretty(_join(prefix, r["path"]))
                    break
    seen, uniq = set(), []
    for r in out:
        k = (r["method"], r["path"], r["file"], r["handler"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ───────────────────────── symbols (for Modules table of non-Python code) ─────────────────────────
SYM = {
    "js": [("class", r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)"),
           ("function", r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+(\w+)"),
           ("function", r"^\s*export\s+(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>"),
           ("interface", r"^\s*(?:export\s+)?interface\s+(\w+)"), ("type", r"^\s*export\s+type\s+(\w+)")],
    "go": [("func", r"^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)\s*\("), ("type", r"^type\s+(\w+)\s+(?:struct|interface)")],
    "jvm": [("type", r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|open\s+|abstract\s+|final\s+|sealed\s+|data\s+|static\s+)*(?:class|interface|enum|record|object)\s+(\w+)"),
            ("method", r"^\s*public\s+(?:static\s+)?(?:final\s+)?[\w<>\[\],.? ]+\s+(\w+)\s*\(")],
    "cs": [("type", r"^\s*(?:public\s+|internal\s+|abstract\s+|sealed\s+|static\s+|partial\s+)*(?:class|interface|enum|record|struct)\s+(\w+)"),
           ("method", r"^\s*public\s+(?:static\s+|async\s+|virtual\s+|override\s+)*[\w<>\[\],.?]+\s+(\w+)\s*\(")],
    "rust": [("fn", r"^\s*pub\s+(?:async\s+)?fn\s+(\w+)"), ("type", r"^\s*pub\s+(?:struct|enum|trait)\s+(\w+)")],
    "ruby": [("class", r"^\s*(?:class|module)\s+([\w:]+)"), ("method", r"^\s*def\s+(?:self\.)?(\w+)")],
    "php": [("class", r"^\s*(?:abstract\s+|final\s+)?(?:class|interface|trait)\s+(\w+)"), ("method", r"^\s*public\s+(?:static\s+)?function\s+(\w+)")],
    "other": [("type", r"^\s*(?:class|struct|enum|protocol|interface|trait|object)\s+(\w+)"), ("function", r"^\s*(?:func|def|fun|fn|function)\s+(\w+)")],
}
SYM_GROUP = {".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".vue": "js", ".svelte": "js",
             ".go": "go", ".java": "jvm", ".kt": "jvm", ".scala": "jvm", ".cs": "cs", ".rs": "rust", ".rb": "ruby", ".php": "php"}


def scan_symbols(files):
    out = {}
    for f in files:
        ext = Path(f).suffix.lower()
        lang = LANG.get(ext)
        if ext == ".py" or not lang or lang in NON_CODE or is_test_path(f):
            continue
        t = text(f)
        if not t:
            continue
        syms = []
        for kind, rx in SYM[SYM_GROUP.get(ext, "other")]:
            for m in re.finditer(rx, t, re.M):
                name = m.group(1)
                if name not in [s[1] for s in syms]:
                    syms.append((kind, name, _line(t, m.start())))
        if syms:
            out[f] = sorted(syms, key=lambda s: s[2])[:14]
    return out


# ───────────────────────── imports → component graph ─────────────────────────
JS_IMPORT = re.compile(r"(?:\bfrom\s+|\brequire\(\s*|\bimport\(\s*|\bimport\s+)" + Q + r"([^'\"]+)" + Q)
JS_TRY = ["", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
          "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]


def import_edges(files):
    fileset, edges = set(files), []
    gomods, fqn, bydir = {}, {}, defaultdict(list)
    for f in files:
        bydir[posixpath.dirname(f)].append(f)
        if f.endswith("go.mod"):
            m = re.search(r"^module\s+(\S+)", text(f), re.M)
            if m:
                gomods[posixpath.dirname(f)] = m.group(1)
        if f.endswith((".java", ".kt", ".scala")):
            m = re.search(r"^\s*package\s+([\w.]+)", text(f), re.M)
            if m:
                fqn[m.group(1) + "." + Path(f).stem] = f
    for f in files:
        ext, t = Path(f).suffix.lower(), text(f)
        if not t or is_test_path(f) or ext == ".py":
            continue
        base = posixpath.dirname(f)
        if ext in JS_EXT:
            for spec in JS_IMPORT.findall(t):
                roots = []
                if spec.startswith("."):
                    roots = [posixpath.normpath(posixpath.join(base, spec))]
                elif spec.startswith(("@/", "~/")):
                    roots = ["src/" + spec[2:], spec[2:]]
                for r in roots:
                    hit = next((r + s for s in JS_TRY if (r + s) in fileset), None)
                    if hit:
                        edges.append((f, hit))
                        break
        elif ext == ".go":
            blocks = re.findall(r"import\s*\((.*?)\)", t, re.S) + re.findall(r"import\s+(?:\w+\s+)?(\"[^\"]+\")", t)
            for b in blocks:
                for p in re.findall(r"\"([^\"]+)\"", b):
                    for mdir, mod in gomods.items():
                        if p == mod or p.startswith(mod + "/"):
                            d = posixpath.normpath(posixpath.join(mdir, p[len(mod):].lstrip("/")))
                            if bydir.get(d):
                                edges.append((f, sorted(bydir[d])[0]))
        elif ext in (".java", ".kt", ".scala"):
            for imp in re.findall(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;?\s*$", t, re.M):
                parts = imp.split(".")
                for k in range(len(parts), 1, -1):
                    cand = ".".join(parts[:k])
                    if cand in fqn:
                        if fqn[cand] != f:
                            edges.append((f, fqn[cand]))
                        break
        elif ext in (".c", ".cc", ".cpp", ".h", ".hpp"):
            for inc in re.findall(r"#include\s+\"([^\"]+)\"", t):
                for cand in (posixpath.normpath(posixpath.join(base, inc)), inc):
                    if cand in fileset:
                        edges.append((f, cand))
                        break
        elif ext == ".rb":
            for rel in re.findall(r"require_relative\s+" + Q + r"([^'\"]+)" + Q, t):
                cand = posixpath.normpath(posixpath.join(base, rel))
                for c in (cand, cand + ".rb"):
                    if c in fileset:
                        edges.append((f, c))
                        break
    return edges


def component_of(path, depth, offset=0):
    parts = path.split("/")[:-1]
    return "/".join(parts[:offset + depth]) or "(root)"


def components(files, edges):
    """Group non-Python code files into <=18 folder-level components with edges between them.
    The folder chain shared by ALL code files (e.g. src/main/java/com/acme/shop) is skipped so the
    components are the meaningful sub-folders. Returns (nodes, edges, shared_prefix) or None."""
    code = [f for f in files if lang_of(f) and lang_of(f) not in NON_CODE and lang_of(f) not in ("Python", "Dockerfile") and not is_test_path(f)]
    if not code:
        return None
    dirs = [f.split("/")[:-1] for f in code]
    common = []
    for segs in zip(*dirs):
        if len(set(segs)) == 1:
            common.append(segs[0])
        else:
            break
    if len(common) == min(len(d) for d in dirs) and len({tuple(d) for d in dirs}) > 1:
        common = common[:-1] if common else common  # keep at least one level to split on
    off = len(common)
    depth = 1
    for d in (3, 2, 1):
        if len({component_of(f, d, off) for f in code}) <= 18:
            depth = d
            break
    info = defaultdict(lambda: [0, Counter()])
    for f in code:
        c = component_of(f, depth, off)
        info[c][0] += 1
        info[c][1][lang_of(f)] += 1
    ce = Counter()
    for a, b in edges:
        ca, cb = component_of(a, depth, off), component_of(b, depth, off)
        if ca != cb and ca in info and cb in info:
            ce[(ca, cb)] += 1
    nodes = {c: (v[0], v[1].most_common(1)[0][0]) for c, v in info.items()}
    return nodes, ce, "/".join(common)


# ───────────────────────── data models ─────────────────────────
def _balanced(t, start):
    depth, i = 0, start
    while i < len(t):
        if t[i] == "(":
            depth += 1
        elif t[i] == ")":
            depth -= 1
            if depth == 0:
                return t[start + 1:i]
        i += 1
    return ""


def scan_models(files):
    out = []
    for f in files:
        ext, t = Path(f).suffix.lower(), text(f)
        if not t or is_test_path(f):
            continue
        if ext == ".sql":
            for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\(", t, re.I):
                body = _balanced(t, m.end() - 1)
                cols, depth, cur = [], 0, ""
                for ch in body + ",":
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    if ch == "," and depth == 0:
                        tok = cur.strip().split()
                        if tok and tok[0].upper().strip("`\"") not in ("PRIMARY", "FOREIGN", "CONSTRAINT", "UNIQUE", "KEY", "INDEX", "CHECK"):
                            cols.append(tok[0].strip("`\"[]"))
                        cur = ""
                    else:
                        cur += ch
                out.append(dict(name=m.group(1), kind="SQL table", file=f, fields=cols[:10]))
        elif f.endswith(".prisma"):
            for m in re.finditer(r"^model\s+(\w+)\s*\{(.*?)^\}", t, re.M | re.S):
                fields = [ln.split()[0] for ln in m.group(2).splitlines() if ln.strip() and not ln.strip().startswith(("@@", "//"))]
                out.append(dict(name=m.group(1), kind="Prisma model", file=f, fields=fields[:10]))
        elif ext in JS_EXT or ext in (".java", ".kt", ".cs"):
            for m in re.finditer(r"@Entity\b(?:\([^)]*\))?\s*(?:@\w+(?:\([^)]*\))?\s*)*(?:export\s+|public\s+|open\s+|data\s+)*(?:abstract\s+)?class\s+(\w+)", t):
                out.append(dict(name=m.group(1), kind="Entity", file=f, fields=[]))
            for m in re.finditer(r"mongoose\.model\(\s*" + Q + r"(\w+)" + Q, t):
                out.append(dict(name=m.group(1), kind="Mongoose model", file=f, fields=[]))
    return out


# ───────────────────────── test counting ─────────────────────────
TEST_CASE = re.compile(r"(?:\b(?:it|test|describe)\s*\(|\bfunc\s+Test\w+|@Test\b|\[(?:Fact|Test|TestMethod)\]|\bdef\s+test_\w+|#\[test\]|\bfn\s+test_\w+)")


def scan_tests(files):
    out = []
    for f in files:
        if f.endswith(".py") or not is_test_path(f) or lang_of(f) in (None, "Markdown", "JSON", "YAML"):
            continue
        t = text(f)
        if t:
            out.append((f, len(TEST_CASE.findall(t))))
    return out


# ───────────────────────── dependency manifests ─────────────────────────
def scan_manifests(files):
    deps = []
    for f in files:
        n = Path(f).name
        t = text(f)
        if not t:
            continue
        if n == "go.mod":
            for m in re.finditer(r"^\s*(?:require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", t, re.M):
                deps.append((m.group(1), m.group(2), f))
        elif n == "Cargo.toml":
            sect = re.search(r"\[dependencies\](.*?)(?:\n\[|\Z)", t, re.S)
            for m in re.finditer(r"^([\w\-]+)\s*=\s*(?:\"([^\"]+)\"|\{[^}]*version\s*=\s*\"([^\"]+)\")", sect.group(1) if sect else "", re.M):
                deps.append((m.group(1), m.group(2) or m.group(3), f))
        elif n == "pom.xml":
            for m in re.finditer(r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]+)</version>)?", t):
                deps.append((f"{m.group(1)}:{m.group(2)}", m.group(3) or "managed", f))
        elif n in ("build.gradle", "build.gradle.kts"):
            for m in re.finditer(r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*['\"]([^'\":]+:[^'\":]+)(?::([^'\"]+))?['\"]", t):
                deps.append((m.group(1), m.group(2) or "managed", f))
        elif n == "Gemfile":
            for m in re.finditer(r"^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", t, re.M):
                deps.append((m.group(1), m.group(2) or "any", f))
        elif n == "composer.json":
            try:
                for sect in ("require", "require-dev"):
                    deps += [(k, v, f"{f} ({sect})") for k, v in json.loads(t).get(sect, {}).items()]
            except ValueError:
                pass
        elif n.endswith(".csproj"):
            for m in re.finditer(r"<PackageReference\s+Include=\"([^\"]+)\"(?:\s+Version=\"([^\"]+)\")?", t):
                deps.append((m.group(1), m.group(2) or "managed", f))
        elif re.fullmatch(r"requirements[\w\-]*\.txt", n) and n != "requirements.txt":
            for line in t.splitlines():
                m = re.match(r"([A-Za-z0-9_.\-]+)\s*(.*)", line.split("#")[0].strip())
                if m and not line.strip().startswith("-"):
                    deps.append((m.group(1), m.group(2).strip() or "any", f))
        elif n == "package.json" and "/" in f:  # nested package.json (monorepo); root handled in analyzer
            try:
                pj = json.loads(t)
                for sect in ("dependencies", "devDependencies"):
                    deps += [(k, v, f"{f} ({sect})") for k, v in pj.get(sect, {}).items()]
            except ValueError:
                pass
    return deps


# ───────────────────────── build / CI / deploy / infra / commands ─────────────────────────
def _yaml(t):
    try:
        import yaml
        return list(yaml.safe_load_all(t))
    except Exception:
        return []


def scan_dockerfiles(files):
    out = []
    for f in files:
        if lang_of(f) != "Dockerfile":
            continue
        t = text(f)
        bases = re.findall(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)", t, re.M | re.I)
        ports = re.findall(r"^EXPOSE\s+(.+)$", t, re.M | re.I)
        cmd = re.findall(r"^(?:CMD|ENTRYPOINT)\s+(.+)$", t, re.M | re.I)
        out.append(dict(file=f, bases=bases, ports=" ".join(ports), cmd=cmd[-1][:80] if cmd else "—"))
    return out


def scan_compose(files):
    out = []
    for f in files:
        if re.fullmatch(r"(docker-)?compose[\w.\-]*\.ya?ml", Path(f).name):
            for doc in _yaml(text(f)):
                if isinstance(doc, dict) and isinstance(doc.get("services"), dict):
                    svcs = {}
                    for name, s in doc["services"].items():
                        s = s or {}
                        dep = s.get("depends_on") or []
                        dep = list(dep) if isinstance(dep, (list, dict)) else [dep]
                        svcs[name] = dict(image=s.get("image") or ("build: " + str(s.get("build")) if s.get("build") else "—"),
                                          ports=[str(p) for p in (s.get("ports") or [])], depends_on=[str(d) for d in dep])
                    out.append(dict(file=f, services=svcs))
    return out


def scan_workflows(files):
    out = []
    for f in files:
        if re.match(r"\.github/workflows/[^/]+\.ya?ml$", f):
            for doc in _yaml(text(f)):
                if isinstance(doc, dict):
                    on = doc.get("on", doc.get(True))
                    trig = list(on) if isinstance(on, (dict, list)) else [on] if on else []
                    jobs = list((doc.get("jobs") or {}).keys())
                    out.append(dict(file=f, name=doc.get("name") or Path(f).stem, triggers=[str(x) for x in trig], jobs=[str(j) for j in jobs]))
        elif Path(f).name in (".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".circleci/config.yml", "cloudbuild.yaml"):
            out.append(dict(file=f, name=Path(f).name, triggers=[], jobs=[]))
    return out


def scan_infra(files):
    out = []
    for f in files:
        ext = Path(f).suffix.lower()
        t = text(f)
        if not t:
            continue
        if ext == ".tf":
            for m in re.finditer(r"^resource\s+\"([^\"]+)\"\s+\"([^\"]+)\"", t, re.M):
                out.append(("Terraform", m.group(1), m.group(2), f))
        elif ext in (".yml", ".yaml") and "apiVersion:" in t and re.search(r"^kind:", t, re.M):
            for doc in _yaml(t):
                if isinstance(doc, dict) and doc.get("kind") and doc.get("apiVersion"):
                    out.append(("Kubernetes", str(doc["kind"]), str((doc.get("metadata") or {}).get("name", "—")), f))
    return out[:60]


def scan_commands(files):
    out = []
    for f in files:
        n = Path(f).name
        t = text(f)
        if not t:
            continue
        if n == "package.json":
            try:
                for k, v in (json.loads(t).get("scripts") or {}).items():
                    out.append((f"npm run {k}", str(v), f))
            except ValueError:
                pass
        elif n in ("Makefile", "makefile", "GNUmakefile"):
            for m in re.finditer(r"^([A-Za-z0-9][\w.\-]*):(?!=)", t, re.M):
                if m.group(1) not in (".PHONY", "all") or m.group(1) == "all":
                    out.append((f"make {m.group(1)}", "", f))
        elif n == "pyproject.toml":
            sect = re.search(r"\[project\.scripts\](.*?)(?:\n\[|\Z)", t, re.S)
            for m in re.finditer(r"^([\w\-]+)\s*=\s*\"([^\"]+)\"", sect.group(1) if sect else "", re.M):
                out.append((m.group(1), m.group(2), f))
        elif n in ("justfile", "Justfile", "Taskfile.yml"):
            out.append((n, "see file", f))
    return out[:60]

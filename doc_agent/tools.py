"""Tools for the doc agent.

The doc is made of independently-owned blocks:
  <!-- AUTO:name -->       generated from the code on EVERY run by analyzer.py (never by the LLM)
  <!-- NARRATIVE:name -->  overview / architecture_notes / setup_usage, written by the LLM when warranted
  <!-- AGENT:slug -->      extra sections + diagrams the LLM decides this repo needs (any topic)
Anything outside the blocks belongs to humans and is never touched.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from . import analyzer as az

DOC_PATH = os.getenv("DOC_PATH", "docs/DOCUMENTATION.md")
MAX_DIFF_CHARS = int(os.getenv("MAX_DIFF_CHARS", "60000"))
MAX_FILE_CHARS = 20000
CHANGELOG_ON = os.getenv("DOC_CHANGELOG", "").lower() in ("1", "true", "yes")
PENDING = "_Pending: the documentation agent will write this section._"
NOT_APPLICABLE = "_Not applicable: nothing of this kind is detected in the repo right now._"
NARRATIVE = {"overview", "architecture_notes", "setup_usage"}

ORDER = [  # (heading, kind, name) - the order sections appear in the doc
    ("Overview", "NARRATIVE", "overview"),
    ("At a Glance", "AUTO", "glance"),
    ("Architecture", "AUTO", "architecture"),
    ("Architecture", "NARRATIVE", "architecture_notes"),
    ("Request Flows", "AUTO", "flows"),
    ("Project Structure", "AUTO", "structure"),
    ("API Reference", "AUTO", "api"),
    ("Data Model", "AUTO", "data"),
    ("Modules", "AUTO", "modules"),
    ("Dependencies", "AUTO", "dependencies"),
    ("Configuration", "AUTO", "env"),
    ("Build, CI/CD & Deployment", "AUTO", "deployment"),
    ("Commands & Scripts", "AUTO", "commands"),
    ("Setup & Usage", "NARRATIVE", "setup_usage"),
    ("Tests", "AUTO", "tests"),
    ("Recent Changes", "AUTO", "changelog"),
]
_last_changes: list = []
_touched: set = set()  # files changed this run; run_agent writes them to a manifest the workflow commits


# ───────────── block helpers ─────────────
def _block(kind, name, content):
    return f"<!-- {kind}:{name} -->\n{content}\n<!-- /{kind}:{name} -->"


def _rx(kind, name):
    return re.compile(rf"(<!-- {kind}:{re.escape(name)} -->\n)(.*?)(\n<!-- /{kind}:{re.escape(name)} -->)", re.S)


def _get_block(doc, kind, name):
    m = _rx(kind, name).search(doc)
    return m.group(2) if m else None


def _replace_block(doc, kind, name, content):
    return _rx(kind, name).sub(lambda m: m.group(1) + content + m.group(3), doc, count=1)


def _insert_block(doc, kind, name, content):
    """Insert a new block at the right position (AGENT blocks go at the end)."""
    block = _block(kind, name, content)
    if kind == "AGENT":
        return doc.rstrip("\n") + "\n\n" + block + "\n"
    idx = next(i for i, (_, k, n) in enumerate(ORDER) if k == kind and n == name)
    heading = ORDER[idx][0]
    for j in range(idx - 1, -1, -1):  # after the nearest earlier block that exists
        h, k, n = ORDER[j]
        m = _rx(k, n).search(doc)
        if m:
            lead = "\n\n" if h == heading else f"\n\n## {heading}\n\n"
            return doc[:m.end()] + lead + block + doc[m.end():]
    for j in range(idx + 1, len(ORDER)):  # else before the nearest later block that exists
        h, k, n = ORDER[j]
        m = _rx(k, n).search(doc)
        if m:
            pos = m.start()
            if h == heading:
                return doc[:pos] + block + "\n\n" + doc[pos:]
            hp = doc.rfind("\n## ", 0, pos)
            pos = hp + 1 if hp >= 0 else pos
            return doc[:pos] + f"## {heading}\n\n" + block + "\n\n" + doc[pos:]
    return doc.rstrip("\n") + f"\n\n## {heading}\n\n" + block + "\n"


def _banner():
    name = os.getenv("GITHUB_REPOSITORY", "").split("/")[-1] or pathlib.Path.cwd().name
    return (f"# {name} — Documentation\n\n"
            "> Maintained by **doc-agent**. `AUTO:*` blocks are regenerated from the code on every push; "
            "`NARRATIVE:*` and `AGENT:*` blocks are written by an AI agent. Add your own text anywhere "
            "outside these blocks and it will be preserved.\n")


def _read():
    p = pathlib.Path(DOC_PATH)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _write(text):
    _touched.add(DOC_PATH)
    p = pathlib.Path(DOC_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _demote_headings(text):
    """Keep agent-written headings below the doc's own '##' sections (without touching code fences)."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,2}\s", line):
            line = "###" + line.lstrip("#")
        out.append(line)
    return "\n".join(out)


def _agent_sections(doc):
    return {m.group(1): (m.group(2).splitlines() or [""])[0].lstrip("# ").strip()
            for m in re.finditer(r"<!-- AGENT:(\w+) -->\n(.*?)\n<!-- /AGENT:\1 -->", doc, re.S)}


def _pending(doc):
    return [n for n in sorted(NARRATIVE) if (_get_block(doc, "NARRATIVE", n) or PENDING).strip() == PENDING]


# ───────────── deterministic refresh (called by run_agent.py, not by the LLM) ─────────────
def refresh_auto_sections() -> str:
    """Regenerate every AUTO block from the code. Sections that do not apply to this repo are left out."""
    global _last_changes
    original = _read()
    doc = original or _banner()
    sections = az.build_sections(az.analyze(), changelog=CHANGELOG_ON)
    changed = []
    for _, kind, name in ORDER:
        cur = _get_block(doc, kind, name)
        if kind == "NARRATIVE":
            if cur is None:
                doc = _insert_block(doc, kind, name, PENDING)
        elif name in sections:
            content = sections[name]
            if content is None:
                if cur not in (None, NOT_APPLICABLE):
                    doc = _replace_block(doc, kind, name, NOT_APPLICABLE)
                    changed.append(name)
            elif cur is None:
                doc = _insert_block(doc, kind, name, content)
                changed.append(name)
            elif cur != content:
                doc = _replace_block(doc, kind, name, content)
                changed.append(name)
    if doc != original:
        _write(doc)
    _last_changes = changed
    state = "Created" if not original else ("Updated" if doc != original else "Unchanged")
    return f"{state} {DOC_PATH}. AUTO sections changed: {changed or 'none'}. Pending narrative: {_pending(doc) or 'none'}."


# ───────────── stale reference check (catches renames / deletions in prose) ─────────────
_EXT = "py|js|jsx|ts|tsx|go|java|kt|rs|rb|php|cs|cpp|c|h|md|json|ya?ml|toml|txt|sh|sql|tf|html|css|xml|gradle|proto|prisma"
_REF = re.compile(rf"`((?:[\w@.\-]+/)*[\w@.\-]+\.(?:{_EXT})|(?:[\w@.\-]+/)+)`")


def _exists(token, files):
    t = token.rstrip("/")
    if pathlib.Path(t).exists() or t in files:
        return True
    if "/" not in t:
        return any(f.rsplit("/", 1)[-1] == t for f in files)
    return any(f.startswith(t + "/") or f.endswith("/" + t) for f in files)


def find_stale_references() -> dict:
    """List file/folder paths mentioned in prose (agent-owned blocks, human text, other existing docs) that no longer exist."""
    doc = _read()
    files = set(az.list_files())
    owned, existing = {}, {}
    for f in _existing_doc_paths()[:40]:
        try:
            t = pathlib.Path(f).read_text(errors="ignore")[:100_000]
        except OSError:
            continue
        bad = sorted({x for x in _REF.findall(t) if not _exists(x, files)})
        if bad:
            existing[f] = bad[:15]
    if not doc:
        return {"in_owned_blocks": {}, "in_manual_text": [], "in_existing_docs": existing}
    for kind in ("NARRATIVE", "AGENT"):
        for m in re.finditer(rf"<!-- {kind}:(\w+) -->\n(.*?)\n<!-- /{kind}:\1 -->", doc, re.S):
            bad = sorted({t for t in _REF.findall(m.group(2)) if not _exists(t, files)})
            if bad:
                owned[f"{kind}:{m.group(1)}"] = bad
    rest = re.sub(r"<!-- (AUTO|NARRATIVE|AGENT):(\w+) -->\n.*?\n<!-- /\1:\2 -->", "", doc, flags=re.S)
    manual = sorted({t for t in _REF.findall(rest) if not _exists(t, files)})
    return {"in_owned_blocks": owned, "in_manual_text": manual[:30], "in_existing_docs": existing}


# ───────────── existing human-written docs (README, docs/*.md, ADRs ...) ─────────────
_DOC_EXT = (".md", ".mdx", ".rst", ".adoc")
_SKIP_DOC = re.compile(r"(?i)^(changelog|changes|history|license|licence|notice|code_of_conduct|security|authors|contributors)(\.\w+)?$")


def _existing_doc_paths():
    out = az._git("-c", "core.quotepath=off", "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    docs = []
    gen = {DOC_PATH} | {str(pathlib.Path(DOC_PATH).with_suffix("." + e)) for e in FORMATS}
    for f in sorted(set(out)):
        parts, name = f.split("/"), f.rsplit("/", 1)[-1]
        if f in gen or any(p in az.SKIP_DIRS for p in parts) or f.endswith(az.LOCKFILES) or _SKIP_DOC.match(name):
            continue
        if not pathlib.Path(f).is_file():
            continue
        is_txt = name.lower().endswith(".txt") and (parts[0] == "docs" or name.lower().startswith(("readme", "contributing")))
        if name.lower().endswith(_DOC_EXT) or is_txt:
            docs.append(f)
    return docs


def list_existing_docs() -> list[str]:
    """List documentation files humans already wrote (README, docs/**, CONTRIBUTING, ADRs, guides) with size and
    first heading. Excludes the generated doc, CHANGELOG, LICENSE and similar. Edit them with edit_doc_file."""
    rows = []
    for f in _existing_doc_paths()[:100]:
        try:
            t = pathlib.Path(f).read_text(errors="ignore")
        except OSError:
            continue
        h = next((l.lstrip("#= ").strip() for l in t.splitlines() if l.strip()), "")
        rows.append(f"{f} ({len(t)} chars) - {h[:70]}")
    return rows


def edit_doc_file(path: str, old_str: str, new_str: str) -> str:
    """Make ONE small edit in an existing human-written doc (see list_existing_docs): replace old_str (must appear
    exactly once, copy it exactly) with new_str. Use for outdated statements only; never rewrite whole files."""
    if path not in _existing_doc_paths():
        return "ERROR: not an editable existing doc. Use list_existing_docs to see valid paths."
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    n = text.count(old_str)
    if not old_str.strip():
        return "ERROR: old_str is empty"
    if n != 1:
        return f"ERROR: old_str appears {n} times (need exactly 1). Include more surrounding context."
    i = text.index(old_str)
    for m in re.finditer(r"<!--\s*manual\s*-->(.*?)<!--\s*/manual\s*-->", text, re.S):
        if m.start() <= i < m.end():
            return "ERROR: that passage is inside a <!-- manual --> block; leave it alone."
    p.write_text(text.replace(old_str, new_str, 1), encoding="utf-8")
    _touched.add(path)
    return f"edited {path}"


# ───────────── tools exposed to the LLM ─────────────
def get_doc_status() -> dict:
    """Doc state: pending narrative sections, AUTO sections changed this run, extra agent sections, stale file references."""
    doc = _read()
    return {"doc_exists": bool(doc), "pending_narrative_sections": _pending(doc),
            "auto_sections_changed_this_run": _last_changes, "agent_sections": _agent_sections(doc),
            "existing_human_docs": list_existing_docs()[:30], "stale_references": find_stale_references()}


def get_repo_overview() -> str:
    """Birds-eye view of ANY repo: languages, top-level folders, manifests, entry points, infra, existing docs."""
    a = az.analyze()
    files = a["files"]
    dirs = {}
    for f in files:
        d = f.split("/")[0] if "/" in f else "(root)"
        dirs[d] = dirs.get(d, 0) + 1
    manifests = [f for f in files if pathlib.Path(f).name in (
        "package.json", "requirements.txt", "pyproject.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
        "Cargo.toml", "Gemfile", "composer.json", "Makefile", "Dockerfile", "docker-compose.yml") or f.endswith(".csproj")]
    entry = [f for f in files if re.match(r"(?i)(main|index|app|server|program|manage|__main__|cli)\.\w+$", pathlib.Path(f).name)
             or f.startswith(("cmd/", "bin/"))][:20]
    docs = [f for f in files if re.match(r"(?i)(readme|contributing|architecture|changelog)", pathlib.Path(f).name) or f.startswith("docs/")][:15]
    out = ["LANGUAGES: " + ", ".join(f"{l} ({n} files, {ln} lines)" for l, n, ln in a["langs"][:10]),
           f"TOTAL FILES: {len(files)}", "TOP-LEVEL FOLDERS: " + ", ".join(f"{d} ({n})" for d, n in sorted(dirs.items(), key=lambda x: -x[1])[:25]),
           "MANIFESTS/BUILD FILES: " + ", ".join(manifests[:25]), "ENTRY-POINT CANDIDATES: " + ", ".join(entry),
           "EXISTING DOCS: " + ", ".join(docs),
           f"DETECTED: {len(a['routes'])} endpoints, {len(a['models'])} data models, {len(a['envs'])} env vars, "
           f"{len(a['workflows'])} CI pipelines, {len(a['infra'])} infra resources, {len(a['compose'])} compose files"]
    return "\n".join(out)


def get_repo_facts() -> str:
    """The auto-extracted facts (glance, API, data model, modules, env vars, deployment, structure) to write accurate prose from."""
    s = az.build_sections(az.analyze())
    keys = ("glance", "api", "data", "modules", "env", "dependencies", "deployment", "commands", "structure")
    return "\n\n".join(f"## {k}\n{s[k]}" for k in keys if s.get(k))[:25000]


def read_doc() -> str:
    """Read the current documentation file."""
    return _read()


def _valid(rev):
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"], capture_output=True).returncode == 0


def get_git_diff() -> str:
    """The latest change as a git diff (renames detected; docs/lockfiles excluded).
    Returns NO_RELEVANT_CHANGES, or DIFF_TOO_LARGE / NO_BASE (meaning: rewrite everything from the full repo)."""
    base = os.getenv("BASE_SHA", "")
    if not base or set(base) == {"0"} or not _valid(base):
        base = "HEAD~1"
        if not _valid(base):
            return "NO_BASE: no previous commit to compare with. Treat as a full rewrite from the whole repo."
    rows = az._git("diff", "-M", "--name-status", base, "HEAD").splitlines()
    rows = [r for r in rows if not az.is_noise(r.split("\t")[-1])]
    if not rows:
        return "NO_RELEVANT_CHANGES"
    paths = [p for r in rows for p in r.split("\t")[1:]]
    diff = az._git("diff", "-M", "--unified=3", base, "HEAD", "--", *paths)
    header = "CHANGED FILES (A=added, M=modified, D=deleted, R=renamed old->new):\n" + "\n".join(rows) + "\n\n"
    if len(diff) > MAX_DIFF_CHARS:
        return "DIFF_TOO_LARGE: summary only.\n" + header
    return header + diff


def list_repo_files() -> list[str]:
    """List the files in the repo (docs, lockfiles, build output and tooling excluded)."""
    return az.list_files()


def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
    """Read part of a text file (default: first 400 lines). Use start_line to page through big files."""
    p = pathlib.Path(path).resolve()
    try:
        p.relative_to(pathlib.Path.cwd().resolve())
    except ValueError:
        return "ERROR: path outside repo"
    if not p.is_file():
        return f"ERROR: {path} not found (deleted or renamed?)"
    lines = p.read_text(errors="ignore").splitlines()
    s = max(start_line, 1) - 1
    chunk = "\n".join(lines[s:s + max_lines])[:MAX_FILE_CHARS]
    return f"[{path} lines {s + 1}-{min(s + max_lines, len(lines))} of {len(lines)}]\n{chunk}"


def read_files(paths: list[str]) -> str:
    """Read several files at once (about 8k chars each, 60k total). Prefer this over many read_file calls."""
    out, total = [], 0
    for p in paths[:30]:
        body = read_file(p, 1, 200)[:8000]
        total += len(body)
        if total > 60000:
            out.append("... (budget reached)")
            break
        out.append(f"=== {p} ===\n{body}")
    return "\n\n".join(out)


def set_narrative(section: str, markdown: str) -> str:
    """Write one narrative section. section must be one of: overview, architecture_notes, setup_usage."""
    if section not in NARRATIVE:
        return f"ERROR: section must be one of {sorted(NARRATIVE)}"
    text = _demote_headings(re.sub(r"<!--.*?-->", "", markdown, flags=re.S).strip())
    if not text:
        return "ERROR: empty text"
    doc = _read() or _banner()
    if _get_block(doc, "NARRATIVE", section) is None:
        doc = _insert_block(doc, "NARRATIVE", section, text)
    else:
        doc = _replace_block(doc, "NARRATIVE", section, text)
    _write(doc)
    return f"updated narrative section '{section}'"


def set_section(slug: str, title: str, markdown: str) -> str:
    """Create or replace an extra documentation section about anything this repo needs that the AUTO sections do not
    cover (e.g. 'database_schema', 'cli_commands', 'background_jobs', 'auth_flow', 'deployment_guide', 'ml_pipeline').
    slug: short lowercase id (a-z, 0-9, _), stable across runs. Use the SAME slug to update it later."""
    slug = re.sub(r"[^a-z0-9_]", "_", slug.lower())[:40].strip("_")
    text = _demote_headings(re.sub(r"<!--.*?-->", "", markdown, flags=re.S).strip())
    if not slug or not text or not title.strip():
        return "ERROR: slug, title and markdown are all required"
    content = f"## {title.strip()}\n\n{text}"
    doc = _read() or _banner()
    doc = _insert_block(doc, "AGENT", slug, content) if _get_block(doc, "AGENT", slug) is None else _replace_block(doc, "AGENT", slug, content)
    _write(doc)
    return f"saved agent section '{slug}'"


def delete_section(slug: str) -> str:
    """Delete an extra agent section (by slug) that is no longer relevant, e.g. its feature was removed."""
    doc = _read()
    if _get_block(doc, "AGENT", slug) is None:
        return f"no agent section '{slug}'"
    new = re.sub(rf"\n*<!-- AGENT:{re.escape(slug)} -->\n.*?\n<!-- /AGENT:{re.escape(slug)} -->\n?", "\n", doc, flags=re.S)
    _write(new.rstrip("\n") + "\n")
    return f"deleted agent section '{slug}'"


def set_diagram(slug: str, title: str, nodes: list[dict[str, str]], edges: list[dict[str, str]],
                direction: str = "LR", description: str = "") -> str:
    """Create/replace a Mermaid architecture diagram section. The tool renders valid Mermaid for you.
    nodes: [{"id":"api","label":"REST API","group":"Backend"}]  (group optional, makes a box around nodes)
    edges: [{"from":"web","to":"api","label":"HTTPS"}]  (label optional). direction: LR or TD."""
    ids, lines = {}, [f"flowchart {direction if direction in ('LR', 'TD', 'TB', 'RL', 'BT') else 'LR'}"]
    groups, loose = {}, []
    for i, n in enumerate(nodes[:40]):
        nid = str(n.get("id") or i)
        ids[nid] = f"n{i}"
        node = f'{ids[nid]}["{az._mm(str(n.get("label") or nid)).replace(chr(10), " ")}"]'
        (groups.setdefault(str(n["group"]), []) if n.get("group") else loose).append(node)
    for node in loose:
        lines.append("  " + node)
    for k, (g, items) in enumerate(groups.items()):
        lines.append(f'  subgraph g{k}["{az._mm(g)}"]')
        lines += ["    " + x for x in items]
        lines.append("  end")
    used = 0
    for e in edges[:80]:
        s, t = ids.get(str(e.get("from"))), ids.get(str(e.get("to")))
        if s and t:
            lab = az._mm(str(e.get("label", ""))).replace("\n", " ").strip()
            lines.append(f'  {s} -->{"|" + chr(34) + lab + chr(34) + "|" if lab else ""} {t}')
            used += 1
    if not ids:
        return "ERROR: no nodes given"
    body = (description.strip() + "\n\n" if description.strip() else "") + az._fence("\n".join(lines))
    return set_section(slug, title, body) + f" ({len(ids)} nodes, {used} edges)"


FORMATS = {"pdf": "pdf", "docx": "docx", "html": "html", "epub": "epub", "rst": "rst", "txt": "plain",
           "odt": "odt", "adoc": "asciidoc", "tex": "latex"}
_MERMAID = re.compile(r"```mermaid\n(.*?)\n```", re.S)
_HTML_MERMAID = """<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.esm.min.mjs";
document.querySelectorAll("pre.mermaid, pre > code.mermaid, pre > code.language-mermaid").forEach(function (el) {
  var pre = el.tagName === "PRE" ? el : el.parentElement, d = document.createElement("div");
  d.className = "mermaid"; d.textContent = pre.textContent; pre.replaceWith(d);
});
mermaid.initialize({ startOnLoad: false });
await mermaid.run({ querySelector: "div.mermaid" });
</script>
<style>body{max-width:1000px;margin:2rem auto;padding:0 1rem;font-family:system-ui,sans-serif}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px}pre{overflow-x:auto}</style>
"""
_PDF_HEAD = r"""\usepackage{fvextra}
\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,commandchars=\\\{\}}
\fvset{breaklines=true,fontsize=\footnotesize}
\usepackage{etoolbox}\AtBeginEnvironment{longtable}{\footnotesize}
"""


def _render_diagrams(md, tmp):
    """Opt-in (DOC_RENDER_DIAGRAMS=true + mermaid-cli installed): turn Mermaid blocks into images. Falls back to code."""
    if os.getenv("DOC_RENDER_DIAGRAMS", "").lower() not in ("1", "true", "yes") or not shutil.which("mmdc"):
        return md, 0, len(_MERMAID.findall(md))
    cfg = pathlib.Path(tmp) / "pp.json"
    cfg.write_text('{"args":["--no-sandbox","--disable-setuid-sandbox"]}')
    state = {"i": 0, "ok": 0}

    def sub(m):
        i = state["i"] = state["i"] + 1
        src, out = pathlib.Path(tmp) / f"d{i}.mmd", pathlib.Path(tmp) / f"d{i}.png"
        src.write_text(m.group(1))
        try:
            r = subprocess.run(["mmdc", "-i", str(src), "-o", str(out), "-p", str(cfg), "-b", "white", "-s", "2"],
                               capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and out.exists():
                state["ok"] += 1
                return f"![diagram {i}]({out})"
        except Exception:
            pass
        return m.group(0)

    out = _MERMAID.sub(sub, md)
    return out, state["ok"], len(_MERMAID.findall(out))  # (markdown, rendered, still-as-code)


def convert_doc(fmt: str) -> str:
    """Convert the Markdown doc to another format with pandoc: pdf, docx, html, epub, rst, txt, odt, adoc, tex."""
    fmt = fmt.strip().lower()
    if fmt not in FORMATS:
        return f"ERROR: unsupported format {fmt}. Choose from {sorted(FORMATS)}"
    if not shutil.which("pandoc"):
        return "ERROR: pandoc is not installed"
    src = pathlib.Path(DOC_PATH)
    if not src.exists():
        return "ERROR: no doc to convert"
    out = src.with_suffix("." + fmt)
    side = src.parent / ".doc-agent-outputs.json"  # content hashes: binary outputs embed timestamps, so we
    try:                                            # only rebuild them when the Markdown really changed
        hashes = json.loads(side.read_text())
    except (OSError, ValueError):
        hashes = {}
    clean = re.sub(r"<!--.*?-->\n?", "", src.read_text(encoding="utf-8"), flags=re.S)
    key = hashlib.sha256((clean + "|" + fmt + "|" + os.getenv("DOC_RENDER_DIAGRAMS", "")).encode()).hexdigest()
    if out.exists() and hashes.get(out.name) == key:
        return f"unchanged {out} (source not modified, not rebuilt)"
    with tempfile.TemporaryDirectory() as tmp:
        md = clean
        md, rendered, as_code = _render_diagrams(md, tmp)
        tmp_md = pathlib.Path(tmp) / "doc.md"
        tmp_md.write_text(md, encoding="utf-8")
        cmd = ["pandoc", str(tmp_md), "-f", "gfm", "-t", FORMATS[fmt], "-o", str(out), "--metadata", "title=Documentation"]
        if fmt == "pdf":
            head = pathlib.Path(tmp) / "head.tex"
            head.write_text(_PDF_HEAD)
            cmd += ["--pdf-engine=xelatex", "-V", "mainfont=DejaVu Sans", "-V", "monofont=DejaVu Sans Mono",
                    "-V", "geometry:margin=0.7in", "-V", "fontsize=9pt", "-V", "colorlinks=true", "-H", str(head), "--toc"]
        elif fmt == "html":
            head = pathlib.Path(tmp) / "head.html"
            head.write_text(_HTML_MERMAID if as_code else "")
            cmd += ["--standalone", "--embed-resources", "-H", str(head), "--toc"]
        elif fmt in ("docx", "odt", "epub"):
            cmd += ["--toc"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return f"ERROR: conversion to {fmt} timed out"
    if r.returncode != 0:
        return f"ERROR: {r.stderr[:400]}"
    hashes[out.name] = key
    side.write_text(json.dumps(hashes, indent=1, sort_keys=True))
    _touched.update({str(out), str(side)})
    note = f", {rendered} diagram(s) rendered as images" if rendered else ""
    return f"created {out}{note}"


def write_manifest() -> None:
    """List the files changed this run, so the workflow commits exactly those (and nothing else)."""
    _touched.add(DOC_PATH) if pathlib.Path(DOC_PATH).exists() else None
    p = pathlib.Path(os.getenv("DOC_TOUCHED_FILE") or ".git/doc-agent-touched")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(sorted(t for t in _touched if pathlib.Path(t).exists())) + "\n")
    except OSError:
        pass

# shop-apis: sample repo with a self-updating documentation agent (doc-agent)

Push code, and `docs/DOCUMENTATION.md` is created or updated automatically (optionally also PDF/DOCX/HTML).
Works for any language and any kind of change. `shop-apis` is just a sample Flask app to try it on.

## How it works
Three layers, so facts are always right and the AI covers whatever static analysis cannot see:

| Layer | Written by | Content |
|---|---|---|
| `<!-- AUTO:* -->` | Code analyzers, **no LLM**, every push | At a Glance, architecture diagrams (modules, folder components, docker-compose topology), request flows, file tree, API table, data model, modules, dependencies, env vars, Docker/CI/Terraform/K8s, commands, tests. Sections that don't apply to your repo are left out. |
| `<!-- NARRATIVE:* -->` | Gemini via ADK | Overview, architecture notes, setup & usage |
| `<!-- AGENT:* -->` | Gemini via ADK | Extra sections and diagrams the agent decides YOUR repo needs (DB schema, auth flow, CLI, jobs, ML pipeline, infra, troubleshooting ...) |

Anything outside these blocks is yours and is never touched.

## Existing docs are kept up to date too
If the repo already has docs (README, `docs/**`, CONTRIBUTING, ADRs, guides in `.md/.mdx/.rst/.adoc/.txt`),
the agent reads them and, when a change makes a statement wrong or incomplete (renamed file or command, new
endpoint or env var, changed setup steps), fixes just that passage with small exact-string edits. It never
rewrites whole files, never touches CHANGELOG/LICENSE or `<!-- manual -->` passages, and flags mentions of
renamed/deleted files. Only files it actually changed are committed.

## Output formats (any document type)
Set repo variable `DOC_FORMATS` (e.g. `pdf,docx,html`). Supported: `pdf docx html epub rst txt odt adoc tex`.
The Markdown is the source of truth; other formats are rebuilt by the pipeline (not by the AI) and only when the
content really changed, so there are no noisy commits. HTML is a single self-contained page whose diagrams render
in the browser. For diagrams as **images inside PDF/DOCX/HTML**, also set `DOC_RENDER_DIAGRAMS=true`
(installs mermaid-cli; falls back to code blocks per diagram if rendering fails).

    push -> workflow -> run_agent.py
      1. refresh AUTO blocks from the code (deterministic, free, idempotent: no change = no commit)
      2. nothing relevant changed? -> stop, no LLM call
      3. else the agent reads `git diff` (renames detected) and updates only what the change affects:
         narrative, extra sections, diagrams; adds sections for new concerns, deletes obsolete ones
      4. stale-reference check: prose that mentions renamed/deleted files is fixed
      5. optional pandoc -> PDF/DOCX/HTML
      6. bot commits docs/

## What is detected automatically
| Area | Languages / formats |
|---|---|
| Endpoints | Flask, FastAPI, Django urls, Express/Koa/Fastify, NestJS, Next.js, Spring, ASP.NET, Go (net/http, chi, gin, echo), Rails, Laravel, OpenAPI/Swagger, protobuf/gRPC |
| Data model | Python models/dataclasses, SQL `CREATE TABLE`, Prisma, JPA/TypeORM `@Entity`, Mongoose |
| Dependencies | requirements.txt, pyproject, package.json, go.mod, Cargo.toml, pom.xml, Gradle, Gemfile, composer.json, .csproj |
| Env vars | Python, Node, Go, Java, Ruby, Rust, PHP, C#, `.env.example`, docker-compose `${VAR}`, Spring `@Value` |
| Architecture | Python import graph + call-flow sequence diagrams; folder-level import graph for JS/TS, Go, Java/Kotlin, C/C++, Ruby; compose service topology |
| Deploy / CI / infra | Dockerfile, docker-compose, GitHub Actions (+ GitLab/Jenkins/Azure listed), Kubernetes, Terraform |
| Commands | package.json scripts, Makefile targets, pyproject scripts |
Everything else (any language, any framework, any concern) is handled by the AI agent from the diff and the files.

## Quick start (local)
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt -r requirements-docs.txt
    export GOOGLE_API_KEY="..."            # not needed for AUTO sections
    git init && git add -A && git commit -m "initial"
    python run_agent.py                     # prints progress, creates docs/DOCUMENTATION.md

## Use it on YOUR repo
Copy `doc_agent/`, `run_agent.py`, `requirements-docs.txt`, `.github/workflows/docs.yml` into the repo root
(next to your existing workflows). Add secret `GOOGLE_API_KEY`, set Settings > Actions > General > Workflow
permissions to **Read and write**. Edit `branches:` in docs.yml if your default branch is not main/master.

## Config (env vars / GitHub repo variables)
`DOC_PATH` (docs/DOCUMENTATION.md), `DOC_MODEL` (gemini-3.8-flash), `DOC_FALLBACK_MODELS` (gemini-3.7-flash),
`DOC_FORMATS` (pdf,docx,html,epub,rst,txt,odt,adoc,tex), `DOC_RENDER_DIAGRAMS` (true = diagram images), `DOC_CHANGELOG` (true adds Recent Changes), `DOC_EXCLUDE` (paths hidden from docs),
`DOC_TREE_MAX_LINES`, `MAX_DIFF_CHARS`, `DOC_DEBUG=1` (full tracebacks).

## Honest limits
- Static analysis is best-effort: dynamic route registration, route prefixes mounted at runtime (Express `app.use('/api', r)`),
  or reflection are not resolved. The agent covers these in prose when it reads the code.
- Diagrams are capped for readability (25 endpoints, 18 folder components, 8 request flows); huge repos get a truncated tree.
- Very large diffs/repos: the agent works from summaries and a file budget, so prose may be partial.
- Without `DOC_RENDER_DIAGRAMS`, PDF/DOCX show diagrams as Mermaid source (HTML and GitHub/VS Code Markdown draw them). The image renderer needs Chrome; it was tested with a stand-in, not real Chrome.
- Protected `main`: swap the last workflow step for `peter-evans/create-pull-request`.
- The LLM step needs Gemini availability; it retries and falls back, and the AUTO sections are updated even if it fails.

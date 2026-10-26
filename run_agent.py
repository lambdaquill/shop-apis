"""CI entry point:  python run_agent.py

Step 1 (deterministic, no API key needed): refresh all AUTO sections from the code.
Step 2 (LLM): write/update the narrative sections, only when something warrants it.
        Retries when Google is busy (503/429) and falls back to another model.
"""
import asyncio
import logging
import os
import pathlib
import sys
import time

from doc_agent import tools

APP, USER = "doc_agent", "ci"
TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "504", "DEADLINE")


def model_list() -> list:
    """Primary model first, then fallbacks (override with DOC_MODEL / DOC_FALLBACK_MODELS)."""
    names = [os.getenv("DOC_MODEL") or "gemini-flash-latest"]
    names += [m.strip() for m in (os.getenv("DOC_FALLBACK_MODELS") or "gemini-flash-latest").split(",")]
    out = []
    for m in names:
        if m and m not in out:
            out.append(m)
    return out


async def run_llm(model: str, first_run: bool, summary: str, attempt_id: int) -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from doc_agent.agent import root_agent

    agent = root_agent.model_copy(update={"model": model})
    session = f"run-{attempt_id}"
    svc = InMemorySessionService()
    await svc.create_session(app_name=APP, user_id=USER, session_id=session)
    runner = Runner(agent=agent, app_name=APP, session_service=svc)
    msg = types.Content(role="user", parts=[types.Part(text=(
        f"FIRST_RUN={first_run}. Pipeline result: {summary} "
        "Now handle the narrative sections following your workflow."))])
    final = ""
    async for event in runner.run_async(user_id=USER, session_id=session, new_message=msg):
        for part in (event.content.parts if event.content and event.content.parts else []):
            if getattr(part, "function_call", None):
                print("  tool call:", part.function_call.name, flush=True)
        if event.is_final_response() and event.content and event.content.parts:
            final = event.content.parts[0].text or ""
    print("AGENT RESULT:", final)


def run_with_retries(first_run: bool, summary: str) -> bool:
    n = 0
    for model in model_list():
        for attempt in range(1, 4):
            n += 1
            print(f"LLM: model={model} attempt {attempt}/3 ...", flush=True)
            try:
                asyncio.run(run_llm(model, first_run, summary, n))
                return True
            except Exception as e:
                text = str(e)
                print(f"  failed: {type(e).__name__}: {text[:220]}", flush=True)
                if any(t in text for t in TRANSIENT) and attempt < 3:
                    wait = 15 * attempt
                    print(f"  Google is busy, retrying in {wait}s ...", flush=True)
                    time.sleep(wait)
                else:
                    break  # not transient (or out of retries): try the next model
    return False


def convert_formats() -> None:
    """Deterministic: build every requested output format (DOC_FORMATS=pdf,docx,html,...)."""
    for fmt in [f.strip() for f in (os.getenv("DOC_FORMATS") or "").split(",") if f.strip()]:
        print("CONVERT:", tools.convert_doc(fmt), flush=True)


def main() -> int:
    if not os.getenv("DOC_DEBUG"):
        logging.disable(logging.CRITICAL)  # hide ADK's long tracebacks; we print a short message instead
    try:
        return _run()
    finally:
        convert_formats()
        tools.write_manifest()  # lists exactly the files the workflow should commit


def _run() -> int:
    first_run = not pathlib.Path(tools.DOC_PATH).exists()
    summary = tools.refresh_auto_sections()
    print("AUTO REFRESH:", summary, flush=True)

    status = tools.get_doc_status()
    stale = status["stale_references"]["in_owned_blocks"]
    if (not first_run and not status["pending_narrative_sections"] and not stale
            and tools.get_git_diff() == "NO_RELEVANT_CHANGES"):
        print("No relevant code changes -> skipping LLM.")
        return 0
    if not os.getenv("GOOGLE_API_KEY") and not os.getenv("GEMINI_API_KEY"):
        print("::warning::No GOOGLE_API_KEY set - narrative sections not updated.")
        return 0
    if not run_with_retries(first_run, summary):
        print("::warning::LLM step failed on all models/attempts. AUTO sections were still updated; "
              "re-run later (set DOC_DEBUG=1 for full details).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

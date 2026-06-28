import os
import sys
import json
import re
import datetime
import requests
from functools import partial
from io import StringIO
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END
from openai import OpenAI
from sqlalchemy import text
from task_store import task_store
from prompt_guard import sanitize_query, validate_plan, is_url_safe, validate_score, SYSTEM_PROMPT_GLOBAL, SYSTEM_PROMPT_RUSSIA, SYSTEM_PROMPT_SCORE, SYSTEM_PROMPT_REPORT


# =====================================================
# STATE
# =====================================================
class ResearchState(TypedDict, total=False):
    query: str
    plan: List[str]
    search_results: List[Dict[str, Any]]
    russia_search_results: List[Dict[str, Any]]
    context: str
    global_analysis: str
    russia_analysis: str
    score: str
    report: str
    logs: List[str]
    fallback_models: List[Dict[str, Any]]
    last_model_used: str


# =====================================================
# LOGGING
# =====================================================
class LogCapture:
    def __init__(self, task_id=None, db_engine=None):
        self.task_id = task_id
        self.lines = []
        self.db_engine = db_engine
        self._first_event_at = None

    def log(self, agent, msg):
        now = datetime.datetime.now(datetime.timezone.utc)
        if self._first_event_at is None:
            self._first_event_at = now
        timestamp = now.astimezone(datetime.timezone(datetime.timedelta(hours=3))).strftime('%H:%M:%S')
        elapsed = round((now - self._first_event_at).total_seconds(), 2)
        line = f"\n{'=' * 90}\n[{timestamp}] {agent}\n{'-' * 90}\n{msg}\n{'=' * 90}"
        self.lines.append(line)
        print(line)
        if self.task_id:
            task_store.append_log(self.task_id, line)
        if self.db_engine and self.task_id:
            try:
                event_type = agent.lower().replace(' ', '_').replace('error', 'error')
                from sqlalchemy import text
                with self.db_engine.connect() as conn:
                    conn.execute(text("""
                        INSERT INTO agent_events (task_id, agent_name, event_type, message, meta, elapsed_seconds)
                        VALUES (CAST(:task_id AS uuid), :agent_name, :event_type, :message, :meta, :elapsed)
                    """), {
                        'task_id': str(self.task_id),
                        'agent_name': agent,
                        'event_type': event_type,
                        'message': msg[:10000],
                        'meta': json.dumps({'timestamp': timestamp}),
                        'elapsed': elapsed,
                    })
                    conn.commit()
            except Exception as e:
                import traceback
                print(f"[AGENT_EVENT ERROR] Failed to write agent_event for agent={agent}: {e}", flush=True)
                traceback.print_exc()


# =====================================================
# HELPERS
# =====================================================
def _clean_fake_links(text):
    dummy_domains = ['example.com', 'example.org', 'example.net', 'test.com', 'domain.com', 'your-source.com']
    for domain in dummy_domains:
        text = re.sub(rf'\(https?://(?:www\.)?{re.escape(domain)}[^)]*\)', '', text)
    text = re.sub(r'\[([^\]]+)\]\(\)', r'\1', text)
    return text


def _ensure_links_in_sources(text, sources):
    if not sources:
        return text
    source_map = {}
    for i, s in enumerate(sources, 1):
        url = s.get("url", "")
        title = s.get("title", "")
        if url and "example" not in url:
            source_map[i] = {"title": title, "url": url}
    if not source_map:
        return text
    pattern = r'(\*\*Источники:\*\*|Источники:)\s*(\*[^*]+\*|-[^-]+)*(.*?)(?=\n\n|\Z)'
    def replace_sources(match):
        header = match.group(1)
        body = match.group(0)
        def replace_ref(m):
            num_str = m.group(1)
            try:
                num = int(num_str)
            except ValueError:
                return m.group(0)
            if num in source_map:
                title = source_map[num]["title"]
                url = source_map[num]["url"]
                if f"({url})" in m.group(0) or f"(http" in m.group(0):
                    return m.group(0)
                return f"[{num}] [{title}]({url})"
            return m.group(0)
        body = re.sub(r'\[(\d+)\]\s+([^\n]+?)(?:\s*\([^)]*\))?', replace_ref, body)
        return body
    text = re.sub(pattern, replace_sources, text, flags=re.DOTALL)
    if "Источники:" not in text:
        sources_block = "\n\n**Источники:**\n"
        for num, info in source_map.items():
            sources_block += f"*   [{num}] [{info['title']}]({info['url']})\n"
        text += sources_block
    return text


# =====================================================
# LLM CALL
# =====================================================
def call_llm(messages, logger: LogCapture, fallback_models=None, state: ResearchState = None):
    global client, MODEL, SEARXNG_URL
    attempts = []
    if MODEL:
        attempts.append((MODEL, client))
    if fallback_models:
        for fm in fallback_models:
            if fm.get('model_name') and fm.get('model_name') != MODEL:
                attempts.append((fm['model_name'], OpenAI(api_key=fm.get('api_key', ''), base_url=fm.get('base_url', ''), timeout=120.0)))

    last_error = None
    for idx, (model_name, llm_client) in enumerate(attempts):
        try:
            if idx > 0:
                logger.log("FALLBACK", f"Переключение на модель: {model_name}")
                if state is not None:
                    state["last_model_used"] = model_name
            else:
                logger.log("LLM CALL", f"Модель: {model_name}")
                if state is not None:
                    state["last_model_used"] = model_name
            resp = llm_client.chat.completions.create(model=model_name, temperature=0, messages=messages)
            out = resp.choices[0].message.content
            logger.log("LLM OUTPUT", out)
            return out
        except Exception as e:
            last_error = e
            logger.log("LLM ERROR", f"Модель {model_name}: {e}")
            continue

    raise last_error or RuntimeError("All LLM models failed")


# =====================================================
# AGENTS
# =====================================================
def planner(state: ResearchState, logger: LogCapture):
    safe_query = sanitize_query(state["query"])
    logger.log("PLANNER", safe_query)
    out = call_llm([
        {"role": "system", "content": "Разбей запрос на 3-5 поисковых запросов. Верни ТОЛЬКО JSON массив строк. Без объяснений."},
        {"role": "user", "content": safe_query}
    ], logger, fallback_models=state.get("fallback_models", []), state=state)
    try:
        plan = json.loads(out)
        plan = validate_plan(plan)
        if not plan:
            plan = [safe_query]
    except Exception:
        plan = [safe_query]
    logger.log("PLANNER_COMPLETED", f"Created {len(plan)} search queries")
    return {"plan": plan}


def search(state: ResearchState, logger: LogCapture):
    logger.log("SEARCH", str(state.get("plan")))
    results = []
    for q in state.get("plan", [state["query"]]):
        try:
            r = requests.get(f"{SEARXNG_URL}/search", params={"q": q, "format": "json"}, timeout=30)
            data = r.json()
            for item in data.get("results", [])[:3]:
                url = item.get("url", "")
                if url and is_url_safe(url):
                    results.append({
                        "query": q,
                        "title": item.get("title", ""),
                        "url": url,
                        "content": item.get("content", "")
                    })
        except Exception as e:
            logger.log("SEARCH ERROR", f"Ошибка при поиске '{q}': {e}")
    logger.log("SEARCH_COMPLETED", f"Found {len(results)} results")
    return {"search_results": results}


def build_context(state: ResearchState, logger: LogCapture):
    logger.log("BUILD_CONTEXT", "Building context from search results")
    results = state.get("search_results", [])
    text = ""
    for i, r in enumerate(results, 1):
        text += f"\n[{i}] {r['title']}\n    URL: {r['url']}\n    СОДЕРЖАНИЕ: {r['content']}\n-----------------\n"
    logger.log("BUILD_CONTEXT_COMPLETED", f"Context built with {len(results)} results")
    return {"context": text}


def global_agent(state: ResearchState, logger: LogCapture):
    sources_block = ""
    for i, r in enumerate(state.get("search_results", []), 1):
        url = r.get("url", "")
        title = r.get("title", "")
        sources_block += f"[{i}] {title} — {url}\n   {r.get('content', '')[:300]}\n\n"
    out = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT_GLOBAL},
        {"role": "user", "content": f"Запрос: {state['query']}\n\nКонтекст (используй ТОЛЬКО эти ссылки):\n{sources_block}"}
    ], logger, fallback_models=state.get("fallback_models", []), state=state)
    out = _clean_fake_links(out)
    out = _ensure_links_in_sources(out, state.get("search_results", []))
    logger.log("GLOBAL_AGENT_COMPLETED", "Global analysis completed")
    return {"global_analysis": out}


def russia_agent(state: ResearchState, logger: LogCapture):
    query = state["query"] + " Россия ИИ рынок"
    russia_results = []
    try:
        r = requests.get(f"{SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=30)
        data = r.json()
        for item in data.get("results", [])[:5]:
            url = item.get("url", "")
            if url and is_url_safe(url):
                russia_results.append({
                    "title": item.get("title", ""),
                    "url": url,
                    "content": item.get("content", "")
                })
        sources_block = ""
        for i, item in enumerate(russia_results, 1):
            sources_block += f"[{i}] {item['title']} — {item['url']}\n   {item['content'][:300]}\n\n"
    except Exception as e:
        logger.log("RUSSIA SEARCH ERROR", str(e))
        sources_block = "Не удалось получить данные по России."
    out = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT_RUSSIA},
        {"role": "user", "content": f"Запрос (Россия): {query}\n\nКонтекст (только эти ссылки):\n{sources_block}"}
    ], logger, fallback_models=state.get("fallback_models", []), state=state)
    out = _clean_fake_links(out)
    out = _ensure_links_in_sources(out, russia_results)
    logger.log("RUSSIA_AGENT_COMPLETED", "Russia analysis completed")
    return {"russia_analysis": out, "russia_search_results": russia_results}


def score_agent(state: ResearchState, logger: LogCapture):
    all_sources = (state.get("search_results", []) or []) + (state.get("russia_search_results", []) or [])
    out = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT_SCORE},
        {"role": "user", "content": state["global_analysis"] + "\n\n" + state["russia_analysis"]}
    ], logger, fallback_models=state.get("fallback_models", []), state=state)
    out = _clean_fake_links(out)
    out = _ensure_links_in_sources(out, all_sources)
    out = validate_score(out)
    logger.log("SCORE_AGENT_COMPLETED", "Score analysis completed")
    return {"score": out}


def report_agent(state: ResearchState, logger: LogCapture):
    all_sources = (state.get("search_results", []) or []) + (state.get("russia_search_results", []) or [])
    out = call_llm([
        {"role": "system", "content": SYSTEM_PROMPT_REPORT},
        {"role": "user", "content": json.dumps({
            "query": state["query"],
            "global_analysis": state.get("global_analysis", ""),
            "russia_analysis": state.get("russia_analysis", ""),
            "score": state.get("score", ""),
            "sources": [
                {"title": r.get("title"), "url": r.get("url")}
                for r in all_sources if r.get("url") and is_url_safe(r.get("url", ""))
            ]
        }, ensure_ascii=False)}
    ], logger, fallback_models=state.get("fallback_models", []), state=state)
    out = _clean_fake_links(out)
    out = _ensure_links_in_sources(out, all_sources)
    logger.log("REPORT_AGENT_COMPLETED", "Report generation completed")
    return {"report": out}


# =====================================================
# LANGGRAPH WORKFLOW
# =====================================================
def build_workflow(logger: LogCapture):
    graph = StateGraph(ResearchState)
    graph.add_node("planner", partial(planner, logger=logger))
    graph.add_node("search", partial(search, logger=logger))
    graph.add_node("build_context", partial(build_context, logger=logger))
    graph.add_node("global", partial(global_agent, logger=logger))
    graph.add_node("russia", partial(russia_agent, logger=logger))
    graph.add_node("score_agent", partial(score_agent, logger=logger))
    graph.add_node("report_agent", partial(report_agent, logger=logger))
    graph.set_entry_point("planner")
    graph.add_edge("planner", "search")
    graph.add_edge("search", "build_context")
    graph.add_edge("build_context", "global")
    graph.add_edge("global", "russia")
    graph.add_edge("russia", "score_agent")
    graph.add_edge("score_agent", "report_agent")
    graph.add_edge("report_agent", END)
    
    # Log all node additions and edges
    logger.log("WORKFLOW", "Graph nodes and edges configured")
    
    return graph.compile()


def run_pipeline(query: str, api_key: str, base_url: str, model: str, searxng_url: str, task_id: str = None, fallback_models=None, db_engine=None):
    global client, MODEL, SEARXNG_URL
    api_key = api_key or os.environ.get('LLM_API_KEY', '')
    base_url = base_url or os.environ.get('LLM_BASE_URL', 'https://bothub.chat/api/v2/openai/v1')
    model = model or os.environ.get('LLM_MODEL', 'gpt-4o-mini')
    searxng_url = searxng_url or os.environ.get('SEARXNG_URL', 'http://searxng.search.svc.cluster.local')

    MODEL = model
    SEARXNG_URL = searxng_url
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=120.0)

    logger = LogCapture(task_id=task_id, db_engine=db_engine)
    logger.log("CONFIG", f"Model: {model}\nBase URL: {base_url}\nSearXNG: {searxng_url}")

    safe_query = sanitize_query(query)
    logger.log("SANITIZE", f"Original: {len(query)} chars -> Safe: {len(safe_query)} chars")

    workflow = build_workflow(logger)
    result = workflow.invoke({"query": safe_query, "fallback_models": fallback_models or []})

    report = result.get("report", "")
    report = _clean_fake_links(report)
    all_sources = (result.get("search_results", []) or []) + (result.get("russia_search_results", []) or [])
    report = _ensure_links_in_sources(report, all_sources)
    result["report"] = report

    for field in ["global_analysis", "russia_analysis", "score"]:
        if result.get(field):
            cleaned = _clean_fake_links(result[field])
            sources_for_field = result.get("russia_search_results", []) if field == "russia_analysis" else result.get("search_results", [])
            cleaned = _ensure_links_in_sources(cleaned, sources_for_field or result.get("search_results", []))
            result[field] = cleaned

    logger.log("DONE", "Pipeline completed")
    return result, logger.lines

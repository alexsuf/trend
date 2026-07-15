import os
import time
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, joinedload
from models import ResearchTask, ResearchReport, TaskStatus, LLMModel, LLMFallback, Base
from pipeline import run_pipeline, run_customer_pipeline
from task_store import task_store


DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
engine = create_engine(DATABASE_URL)

Base.metadata.create_all(engine)


def process_task(task_uuid, prompt, model_id, fallbacks, c_name=None):
    with Session(engine) as db_session:
        model = db_session.scalar(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .where(LLMModel.id == model_id)
        )
        if not model:
            task_store.append_log(str(task_uuid), "ERROR: Model not found")
            return
        
        provider = model.provider
        api_key = provider.api_key or os.environ.get('LLM_API_KEY', '')
        base_url = provider.base_url
        model_name = model.model_name
        
        provider_name = provider.name if provider else '-'
        model_timeout = model.timeout if model.timeout and model.timeout > 0 else 180
        
        task = db_session.scalar(select(ResearchTask).where(ResearchTask.id == task_uuid))
        if task:
            task.status = TaskStatus.running
            task.model_used = f"{provider_name} - {model.display_name or model_name}"
            db_session.commit()
    
    try:
        if c_name:
            result, logs = run_customer_pipeline(
                c_name=c_name,
                query=prompt,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                searxng_url=os.environ.get('SEARXNG_URL', 'http://searxng.search.svc.cluster.local'),
                task_id=str(task_uuid),
                fallback_models=fallbacks or [],
                db_engine=engine,
                timeout=model_timeout,
            )
        else:
            result, logs = run_pipeline(
                query=prompt,
                api_key=api_key,
                base_url=base_url,
                model=model_name,
                searxng_url=os.environ.get('SEARXNG_URL', 'http://searxng.search.svc.cluster.local'),
                task_id=str(task_uuid),
                fallback_models=fallbacks or [],
                db_engine=engine,
                timeout=model_timeout,
            )
        
        with Session(engine) as db_session:
            task = db_session.scalar(select(ResearchTask).where(ResearchTask.id == task_uuid))
            if task:
                task.status = TaskStatus.done
                task.finished_at = func.now()
                
                score_val = 0
                if not c_name:
                    score_text = result.get('score', '')
                    if score_text:
                        import re
                        m = re.search(r'(\d+(?:\.\d+)?)\s*из\s*10', score_text)
                        if m:
                            score_val = int(round(float(m.group(1))))
                
                report_data = {
                    'content': result.get('report', ''),
                    'word_path': None,
                }
                if c_name:
                    report_data['analysis'] = result.get('analysis', '')
                else:
                    report_data['global_analysis'] = result.get('global_analysis', '')
                    report_data['russia_analysis'] = result.get('russia_analysis', '')
                    report_data['score'] = result.get('score', '')
                
                source_lists = [result.get('search_results', []) or []]
                if not c_name:
                    source_lists.append(result.get('russia_search_results', []) or [])
                
                db_session.add(ResearchReport(
                    task_id=task_uuid,
                    report_json=report_data,
                    sources=[
                        {'url': r.get('url', ''), 'title': r.get('title', '')}
                        for lst in source_lists
                        for r in lst
                        if r.get('url') and 'example' not in r.get('url', '')
                    ],
                    score=score_val,
                ))
                db_session.commit()
        
        task_store.append_log(str(task_uuid), "Pipeline completed successfully")
        
    except Exception as e:
        task_store.append_log(str(task_uuid), f"Pipeline error: {str(e)}")
        with Session(engine) as db_session:
            task = db_session.scalar(select(ResearchTask).where(ResearchTask.id == task_uuid))
            if task:
                task.status = TaskStatus.error
                task.error_message = str(e)
                task.finished_at = func.now()
                db_session.commit()


def worker_loop():
    poll_interval = int(os.environ.get('WORKER_POLL_INTERVAL', '2'))
    
    while True:
        try:
            with Session(engine) as db_session:
                task = db_session.execute(
                    select(ResearchTask)
                    .where(ResearchTask.status == TaskStatus.queued)
                    .order_by(ResearchTask.created_at.asc(), ResearchTask.priority.desc())
                    .limit(1)
                    .with_for_update(skip_locked=True)
                ).scalar_one_or_none()
                
                if task:
                    task.status = TaskStatus.running
                    task.started_at = func.now()
                    db_session.commit()
                    
                    meta = task.meta or {}
                    process_task(
                        task.id,
                        task.prompt,
                        meta.get('model_id'),
                        meta.get('fallbacks', []),
                        task.c_name,
                    )
        except Exception as e:
            print(f"Worker error: {e}")
        
        time.sleep(poll_interval)


if __name__ == '__main__':
    worker_loop()

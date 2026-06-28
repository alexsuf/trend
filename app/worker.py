import os
import time
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, joinedload
from models import ResearchTask, ResearchReport, TaskStatus, LLMModel, LLMFallback
from pipeline import run_pipeline
from task_store import task_store


DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
engine = create_engine(DATABASE_URL)


def process_task(task_uuid, prompt, model_id, fallbacks):
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
        
        task = db_session.scalar(select(ResearchTask).where(ResearchTask.id == task_uuid))
        if task:
            task.status = TaskStatus.running
            task.model_used = f"{provider_name} - {model.display_name or model_name}"
            db_session.commit()
    
    try:
        result, logs = run_pipeline(
            query=prompt,
            api_key=api_key,
            base_url=base_url,
            model=model_name,
            searxng_url=os.environ.get('SEARXNG_URL', 'http://searxng.search.svc.cluster.local'),
            task_id=str(task_uuid),
            fallback_models=fallbacks or [],
            db_engine=engine,
        )
        
        with Session(engine) as db_session:
            task = db_session.scalar(select(ResearchTask).where(ResearchTask.id == task_uuid))
            if task:
                task.status = TaskStatus.done
                task.finished_at = func.now()
                db_session.add(ResearchReport(
                    task_id=task_uuid,
                    report_json={
                        'content': result.get('report', ''),
                        'word_path': None,
                    },
                    sources=[
                        {'url': r.get('url', ''), 'title': r.get('title', '')}
                        for r in (result.get('search_results', []) or []) + (result.get('russia_search_results', []) or [])
                        if r.get('url') and 'example' not in r.get('url', '')
                    ],
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
    poll_interval = int(os.environ.get('WORKER_POLL_INTERVAL', '5'))
    
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
                    )
        except Exception as e:
            print(f"Worker error: {e}")
        
        time.sleep(poll_interval)


if __name__ == '__main__':
    worker_loop()

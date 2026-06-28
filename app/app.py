import os
import json
import base64
import datetime
import requests
from flask import Flask, redirect, url_for, session, render_template, request, jsonify, flash, send_file
from authlib.integrations.flask_client import OAuth
from functools import wraps
from sqlalchemy import create_engine, select, func, delete
from sqlalchemy.orm import Session, joinedload
from io import BytesIO
from models import (
    User, ResearchTask, ResearchReport, TaskStatus,
    LLMModel, LLMProvider, LLMGroup, UserGroup,
    GroupModel, LLMFallback, AgentEvent, Base
)
from task_store import task_store
from word_generator import generate_word_report
from pipeline import run_pipeline

app = Flask(__name__, template_folder='templates')
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key-change-in-prod'
app.config['PROPAGATE_EXCEPTIONS'] = True

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
engine = create_engine(DATABASE_URL)

Base.metadata.create_all(engine)

oauth = OAuth(app)

KEYCLOAK_URL = os.environ.get('KEYCLOAK_URL') or 'http://auth.local'
KEYCLOAK_INTERNAL_URL = os.environ.get('KEYCLOAK_INTERNAL_URL') or 'http://keycloak.keycloak.svc.cluster.local'
KEYCLOAK_REALM = os.environ.get('KEYCLOAK_REALM') or 'trend'
KEYCLOAK_CLIENT_ID = os.environ.get('KEYCLOAK_CLIENT_ID') or 'trend-web'
KEYCLOAK_CLIENT_SECRET = os.environ.get('KEYCLOAK_CLIENT_SECRET') or 'bbWGIugaSj9ithjybqoNR5hXI9acjEel'

oauth.register(
    'keycloak',
    server_metadata_url=f'{KEYCLOAK_INTERNAL_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration',
    client_id=KEYCLOAK_CLIENT_ID,
    client_secret=KEYCLOAK_CLIENT_SECRET,
    authorize_url=f'{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/auth',
    client_kwargs={'scope': 'openid profile email roles', 'require_nonce': False},
)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def get_db_session():
    return Session(engine)


def flash_message(message, category='info'):
    flash(message, category)


@app.route('/')
def index():
    if 'user' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/login')
def login():
    redirect_uri = url_for('callback', _external=True)
    return oauth.keycloak.authorize_redirect(redirect_uri)


@app.route('/callback')
def callback():
    try:
        token = oauth.keycloak.authorize_access_token()
        access_token = token.get('access_token', '')

        if access_token:
            parts = access_token.split('.')
            payload = base64.b64decode(parts[1] + '=' * (4 - len(parts[1]) % 4)).decode()
            userinfo = json.loads(payload)
        else:
            userinfo = {}

        realm_roles = []
        if 'realm_access' in userinfo and 'roles' in userinfo['realm_access']:
            realm_roles = userinfo['realm_access']['roles']

        keycloak_id = userinfo.get('sub', '')
        username = userinfo.get('preferred_username', '')
        email = userinfo.get('email', '')

        with get_db_session() as db_session:
            user = db_session.scalar(select(User).where(User.keycloak_id == keycloak_id))
            if not user:
                user = User(keycloak_id=keycloak_id, username=username, email=email)
                db_session.add(user)
                db_session.commit()

        session['user'] = {
            'username': username,
            'email': email,
            'name': userinfo.get('name', username),
            'roles': realm_roles,
            'keycloak_id': keycloak_id,
            'token': access_token,
        }

        return redirect(url_for('dashboard'))
    except Exception as e:
        return f'Ошибка аутентификации: {str(e)}', 400


@app.route('/logout')
def logout():
    session.clear()
    logout_url = (
        f'{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout'
        f'?redirect_uri={url_for("index", _external=True)}'
    )
    return redirect(logout_url)


@app.route('/dashboard')
@login_required
def dashboard():
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))

        total_tasks = db.scalar(
            select(func.count()).select_from(ResearchTask)
            .where(ResearchTask.user_id == user.id)
        )
        completed_tasks = db.scalar(
            select(func.count()).select_from(ResearchTask)
            .where(ResearchTask.user_id == user.id, ResearchTask.status == TaskStatus.done)
        )
        error_tasks = db.scalar(
            select(func.count()).select_from(ResearchTask)
            .where(ResearchTask.user_id == user.id, ResearchTask.status == TaskStatus.error)
        )

        recent_tasks = db.scalars(
            select(ResearchTask)
            .where(ResearchTask.user_id == user.id)
            .order_by(ResearchTask.created_at.desc())
            .limit(10)
        ).all()

        yesterday = datetime.datetime.utcnow() - datetime.timedelta(days=14)
        daily_rows = db.execute(
            select(
                func.date(ResearchTask.created_at).label('day'),
                func.count().label('cnt')
            )
            .where(ResearchTask.user_id == user.id, ResearchTask.created_at >= yesterday)
            .group_by(func.date(ResearchTask.created_at))
            .order_by(func.date(ResearchTask.created_at).desc())
        ).all()
        daily_stats = [(row.day, row.cnt) for row in daily_rows]

    return render_template('app/dashboard.html',
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        error_tasks=error_tasks,
        recent_tasks=recent_tasks,
        daily_stats=daily_stats)


@app.route('/history')
@login_required
def history():
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        tasks = db.scalars(
            select(ResearchTask)
            .where(ResearchTask.user_id == user.id)
            .order_by(ResearchTask.created_at.desc())
        ).all()
    return render_template('app/history.html', tasks=tasks)


@app.route('/history/delete/<uuid:task_id>', methods=['POST'])
@login_required
def history_delete(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        task = db.scalar(
            select(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.user_id == user.id)
        )
        if task:
            db.delete(task)
            db.commit()
            flash_message('Запрос удалён', 'success')
        else:
            flash_message('Запрос не найден', 'danger')
    return redirect(url_for('history'))


@app.route('/query', methods=['GET', 'POST'])
@login_required
def new_query():
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))

        default_model_id = None
        default_model_label = 'Не настроена'

        user_group = db.scalar(
            select(UserGroup).where(UserGroup.user_id == user.id)
        )
        if user_group:
            default_relation = db.scalar(
                select(GroupModel)
                .where(GroupModel.group_id == user_group.group_id, GroupModel.is_default == True)
            )
            if not default_relation:
                default_relation = db.scalar(
                    select(GroupModel)
                    .where(GroupModel.group_id == user_group.group_id)
                    .order_by(GroupModel.relation_number)
                )
            if default_relation:
                model = db.scalar(
                    select(LLMModel)
                    .options(joinedload(LLMModel.provider))
                    .where(LLMModel.id == default_relation.model_id)
                )
                if model:
                    default_model_id = str(model.id)
                    provider_name = model.provider.name if model.provider else '-'
                    default_model_label = f'{provider_name} - {model.display_name or model.model_name}'

        if request.method == 'POST':
            prompt = request.form.get('prompt', '').strip()
            model_id = request.form.get('model_id', '')
            if not prompt:
                flash_message('Введите текст запроса', 'danger')
                return render_template('app/query.html',
                    default_model_id=default_model_id,
                    default_model_label=default_model_label)
            if not model_id or not default_model_id:
                flash_message('Модель не настроена', 'danger')
                return redirect(url_for('new_query'))

            fallback_models = []
            fallbacks = db.scalars(
                select(LLMFallback)
                .options(
                    joinedload(LLMFallback.fallback_model).joinedload(LLMModel.provider)
                )
                .where(LLMFallback.model_id == model_id)
                .order_by(LLMFallback.priority)
            ).all()
            for fb in fallbacks:
                fb_model = fb.fallback_model
                fb_provider = fb_model.provider
                fallback_models.append({
                    'model_name': fb_model.model_name,
                    'api_key': fb_provider.api_key or os.environ.get('LLM_API_KEY', ''),
                    'base_url': fb_provider.base_url,
                })

            task = ResearchTask(
                user_id=user.id,
                prompt=prompt,
                status=TaskStatus.queued,
                meta={'model_id': model_id, 'fallbacks': fallback_models},
            )
            db.add(task)
            db.commit()
            flash_message('Запрос поставлен в очередь', 'success')
            return redirect(url_for('result_view', task_id=task.id))

    return render_template('app/query.html',
        default_model_id=default_model_id,
        default_model_label=default_model_label)


@app.route('/result/<uuid:task_id>')
@login_required
def result_view(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        task = db.scalar(
            select(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.user_id == user.id)
        )
        if not task:
            flash_message('Запрос не найден', 'danger')
            return redirect(url_for('history'))
    return render_template('app/result.html', task_id=str(task_id), prompt=task.prompt)


@app.route('/logs/<uuid:task_id>')
@login_required
def task_log(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        task = db.scalar(
            select(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.user_id == user.id)
        )
        if not task:
            flash_message('Запрос не найден', 'danger')
            return redirect(url_for('history'))
        events = db.scalars(
            select(AgentEvent)
            .where(AgentEvent.task_id == task_id)
            .order_by(AgentEvent.created_at.asc())
        ).all()
    return render_template('app/logs.html', task=task, events=events)


@app.route('/reports/<uuid:task_id>')
@login_required
def report(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        task = db.scalar(
            select(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.user_id == user.id)
        )
        if not task:
            flash_message('Отчёт не найден', 'danger')
            return redirect(url_for('history'))
        report_obj = db.scalar(
            select(ResearchReport).where(ResearchReport.task_id == task_id)
        )
    return render_template('app/report.html', task=task, report=report_obj)


@app.route('/download/<uuid:task_id>')
@login_required
def download(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return redirect(url_for('logout'))
        task = db.scalar(
            select(ResearchTask)
            .where(ResearchTask.id == task_id, ResearchTask.user_id == user.id)
        )
        if not task:
            flash_message('Запрос не найден', 'danger')
            return redirect(url_for('history'))
        report_obj = db.scalar(
            select(ResearchReport).where(ResearchReport.task_id == task_id)
        )
        if not report_obj or not report_obj.report_json:
            flash_message('Отчёт не найден', 'danger')
            return redirect(url_for('report', task_id=task_id))

    report_data = report_obj.report_json
    state = {
        'query': task.prompt,
        'report': report_data.get('content', ''),
        'global_analysis': report_data.get('global_analysis', ''),
        'russia_analysis': report_data.get('russia_analysis', ''),
        'score': report_data.get('score', ''),
    }
    doc = generate_word_report(state)
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    filename = f'report_{task.task_number}.docx'
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    )


@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html')


@app.route('/api/task-report/<uuid:task_id>')
@login_required
def api_task_report(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401
        report_obj = db.scalar(
            select(ResearchReport).where(ResearchReport.task_id == task_id)
        )
        if not report_obj:
            return jsonify({'error': 'Report not found'}), 404
        return jsonify({'report': report_obj.report_json.get('content', '') if report_obj.report_json else ''})


@app.route('/api/task-events/<uuid:task_id>')
@login_required
def api_task_events(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401

        events = db.scalars(
            select(AgentEvent)
            .where(AgentEvent.task_id == task_id)
            .order_by(AgentEvent.created_at.asc())
        ).all()

        result = []
        for e in events:
            result.append({
                'id': str(e.id),
                'agent_name': e.agent_name,
                'event_type': e.event_type,
                'message': e.message,
                'created_at': e.created_at.isoformat() if e.created_at else None,
                'elapsed_seconds': float(e.elapsed_seconds) if e.elapsed_seconds else None,
            })
        return jsonify(result)


@app.route('/api/task-logs/<uuid:task_id>')
@login_required
def api_task_logs(task_id):
    keycloak_id = session['user'].get('keycloak_id')
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.keycloak_id == keycloak_id))
        if not user:
            return jsonify({'error': 'Unauthorized'}), 401
        task = db.scalar(select(ResearchTask).where(ResearchTask.id == task_id))
        if not task:
            return jsonify({'error': 'Task not found'}), 404
        return jsonify({'status': task.status.value, 'model_label': task.model_used or '-'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

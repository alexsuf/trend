import os
import json
import requests
from flask import Flask, redirect, url_for, session, render_template, request, jsonify, flash
from authlib.integrations.flask_client import OAuth
from functools import wraps
from datetime import datetime
from config import Config
from sqlalchemy import create_engine, select, func, delete
from sqlalchemy.orm import Session, joinedload, selectinload
from models import (
    User, LLMGroup, UserGroup, LLMProvider, LLMModel,
    GroupModel, LLMFallback, AgentModel, ResearchScore, Base
)
from sqlalchemy.dialects.postgresql import UUID

app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)
app.secret_key = app.config['SECRET_KEY']

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
engine = create_engine(DATABASE_URL)

Base.metadata.create_all(engine)

oauth = OAuth(app)

KEYCLOAK_URL = app.config['KEYCLOAK_URL']
KEYCLOAK_INTERNAL_URL = app.config['KEYCLOAK_INTERNAL_URL']
KEYCLOAK_REALM = app.config['KEYCLOAK_REALM']
KEYCLOAK_CLIENT_ID = app.config['KEYCLOAK_CLIENT_ID']
KEYCLOAK_CLIENT_SECRET = app.config['KEYCLOAK_CLIENT_SECRET']

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


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        user_roles = session.get('user', {}).get('roles', [])
        if 'administrator' not in user_roles:
            return 'Доступ запрещён. Требуется роль: administrator', 403
        return f(*args, **kwargs)
    return decorated_function


def get_db_session():
    return Session(engine)


def flash_message(message, category='info'):
    flash(message, category)


def get_keycloak_admin_token():
    admin_user = os.environ.get('KEYCLOAK_ADMIN_USER', 'admin')
    admin_pass = os.environ.get('KEYCLOAK_ADMIN_PASS', 'secret')
    url = f'{KEYCLOAK_INTERNAL_URL}/realms/master/protocol/openid-connect/token'
    data = {
        'grant_type': 'password',
        'client_id': 'admin-cli',
        'username': admin_user,
        'password': admin_pass,
    }
    try:
        resp = requests.post(url, data=data, timeout=5)
        resp.raise_for_status()
        return resp.json().get('access_token')
    except Exception as e:
        app.logger.error(f'Failed to get Keycloak admin token: {e}')
        return None


def get_user_roles_from_keycloak(user_id):
    token = get_keycloak_admin_token()
    if not token:
        return []
    try:
        url = f'{KEYCLOAK_INTERNAL_URL}/admin/realms/{KEYCLOAK_REALM}/users/{user_id}/role-mappings/realm'
        headers = {'Authorization': f'Bearer {token}'}
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        roles = resp.json()
        return [r['name'] for r in roles] if roles else []
    except Exception as e:
        app.logger.error(f'Failed to get roles for user {user_id}: {e}')
        return []


@app.route('/')
@login_required
def index():
    user = session.get('user')
    stats = {}
    with get_db_session() as db:
        stats['groups'] = db.scalar(select(func.count()).select_from(LLMGroup))
        stats['users'] = db.scalar(select(func.count()).select_from(User))
        stats['providers'] = db.scalar(select(func.count()).select_from(LLMProvider))
        stats['models'] = db.scalar(select(func.count()).select_from(LLMModel))
    return render_template('admin/index.html', user=user, stats=stats)


@app.route('/login')
def login():
    redirect_uri = url_for('callback', _external=True)
    return oauth.keycloak.authorize_redirect(redirect_uri)


@app.route('/callback')
def callback():
    try:
        token = oauth.keycloak.authorize_access_token()
        access_token = token.get('access_token', '')

        import base64

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
            'token': access_token,
        }

        if 'administrator' not in realm_roles:
            return render_template('admin/access_denied.html'), 403

        return redirect(url_for('index'))
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


# ===================== LLM Groups =====================

@app.route('/groups')
@admin_required
def groups_list():
    with get_db_session() as db:
        groups = db.scalars(select(LLMGroup).order_by(LLMGroup.group_number)).all()
    return render_template('admin/groups/list.html', groups=groups, user=session.get('user'))


@app.route('/groups/create', methods=['GET', 'POST'])
@admin_required
def groups_create():
    if request.method == 'POST':
        with get_db_session() as db:
            group = LLMGroup(
                name=request.form['name'],
                description=request.form.get('description'),
                enabled=request.form.get('enabled') == 'on'
            )
            db.add(group)
            db.commit()
        flash_message('Группа создана', 'success')
        return redirect(url_for('groups_list'))
    return render_template('admin/groups/form.html', user=session.get('user'), group=None)


@app.route('/groups/edit/<uuid:group_id>', methods=['GET', 'POST'])
@admin_required
def groups_edit(group_id):
    with get_db_session() as db:
        group = db.scalar(select(LLMGroup).where(LLMGroup.id == group_id))
        if not group:
            flash_message('Группа не найдена', 'danger')
            return redirect(url_for('groups_list'))

        if request.method == 'POST':
            group.name = request.form['name']
            group.description = request.form.get('description')
            group.enabled = request.form.get('enabled') == 'on'
            db.commit()
            flash_message('Группа обновлена', 'success')
            return redirect(url_for('groups_list'))

    return render_template('admin/groups/form.html', user=session.get('user'), group=group)


@app.route('/groups/delete/<uuid:group_id>', methods=['POST'])
@admin_required
def groups_delete(group_id):
    with get_db_session() as db:
        group = db.scalar(select(LLMGroup).where(LLMGroup.id == group_id))
        if group:
            db.delete(group)
            db.commit()
            flash_message('Группа удалена', 'success')
        else:
            flash_message('Группа не найдена', 'danger')
    return redirect(url_for('groups_list'))


# ===================== Users =====================

@app.route('/users')
@admin_required
def users_list():
    with get_db_session() as db:
        users = db.scalars(select(User).order_by(User.user_number)).all()

    users_with_roles = []
    for user in users:
        roles = get_user_roles_from_keycloak(user.id)
        users_with_roles.append((user, roles))

    return render_template('admin/users/list.html', users_with_roles=users_with_roles, user=session.get('user'))


@app.route('/users/delete/<uuid:user_id>', methods=['POST'])
@admin_required
def users_delete(user_id):
    with get_db_session() as db:
        user = db.scalar(select(User).where(User.id == user_id))
        if user:
            db.delete(user)
            db.commit()
            flash_message('Пользователь удалён', 'success')
        else:
            flash_message('Пользователь не найден', 'danger')
    return redirect(url_for('users_list'))


# ===================== User Groups =====================

@app.route('/user-groups')
@admin_required
def user_groups_list():
    with get_db_session() as db:
        relations = db.scalars(
            select(UserGroup)
            .options(joinedload(UserGroup.user))
            .options(joinedload(UserGroup.group))
            .order_by(UserGroup.relation_number)
        ).all()
    return render_template('admin/user_groups/list.html', relations=relations, user=session.get('user'))


@app.route('/user-groups/create', methods=['GET', 'POST'])
@admin_required
def user_groups_create():
    with get_db_session() as db:
        users = db.scalars(select(User).order_by(User.username)).all()
        groups = db.scalars(select(LLMGroup).order_by(LLMGroup.name)).all()

    if request.method == 'POST':
        with get_db_session() as db:
            relation = UserGroup(
                user_id=request.form['user_id'],
                group_id=request.form['group_id']
            )
            db.add(relation)
            try:
                db.commit()
                flash_message('Связь создана', 'success')
            except Exception:
                db.rollback()
                flash_message('Такая связь уже существует', 'danger')
        return redirect(url_for('user_groups_list'))

    return render_template('admin/user_groups/form.html', user=session.get('user'), relation=None, users=users, groups=groups)


@app.route('/user-groups/delete/<uuid:rel_id>', methods=['POST'])
@admin_required
def user_groups_delete(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(UserGroup).where(UserGroup.id == rel_id))
        if relation:
            db.delete(relation)
            db.commit()
            flash_message('Связь удалена', 'success')
        else:
            flash_message('Связь не найдена', 'danger')
    return redirect(url_for('user_groups_list'))


# ===================== Providers =====================

@app.route('/providers')
@admin_required
def providers_list():
    with get_db_session() as db:
        providers = db.scalars(select(LLMProvider).order_by(LLMProvider.provider_number)).all()
    return render_template('admin/providers/list.html', providers=providers, user=session.get('user'))


@app.route('/providers/create', methods=['GET', 'POST'])
@admin_required
def providers_create():
    if request.method == 'POST':
        with get_db_session() as db:
            provider = LLMProvider(
                name=request.form['name'],
                provider_type=request.form['provider_type'],
                base_url=request.form['base_url'],
                api_key=request.form.get('api_key'),
                enabled=request.form.get('enabled') == 'on'
            )
            db.add(provider)
            db.commit()
        flash_message('Провайдер создан', 'success')
        return redirect(url_for('providers_list'))
    return render_template('admin/providers/form.html', user=session.get('user'), provider=None)


@app.route('/providers/edit/<uuid:provider_id>', methods=['GET', 'POST'])
@admin_required
def providers_edit(provider_id):
    with get_db_session() as db:
        provider = db.scalar(select(LLMProvider).where(LLMProvider.id == provider_id))
        if not provider:
            flash_message('Провайдер не найден', 'danger')
            return redirect(url_for('providers_list'))

        if request.method == 'POST':
            provider.name = request.form['name']
            provider.provider_type = request.form['provider_type']
            provider.base_url = request.form['base_url']
            new_key = request.form.get('api_key', '')
            if new_key:
                provider.api_key = new_key
            provider.enabled = request.form.get('enabled') == 'on'
            db.commit()
            flash_message('Провайдер обновлён', 'success')
            return redirect(url_for('providers_list'))

    return render_template('admin/providers/form.html', user=session.get('user'), provider=provider)


@app.route('/providers/delete/<uuid:provider_id>', methods=['POST'])
@admin_required
def providers_delete(provider_id):
    with get_db_session() as db:
        provider = db.scalar(select(LLMProvider).where(LLMProvider.id == provider_id))
        if provider:
            db.delete(provider)
            db.commit()
            flash_message('Провайдер удалён', 'success')
        else:
            flash_message('Провайдер не найден', 'danger')
    return redirect(url_for('providers_list'))


# ===================== Models =====================

@app.route('/models')
@admin_required
def models_list():
    with get_db_session() as db:
        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.model_number)
        ).all()
    return render_template('admin/models/list.html', models=models, user=session.get('user'))


@app.route('/models/create', methods=['GET', 'POST'])
@admin_required
def models_create():
    with get_db_session() as db:
        providers = db.scalars(select(LLMProvider).order_by(LLMProvider.name)).all()

    if request.method == 'POST':
        with get_db_session() as db:
            model = LLMModel(
                provider_id=request.form['provider_id'],
                model_name=request.form['model_name'],
                display_name=request.form.get('display_name'),
                context_size=int(request.form['context_size']) if request.form.get('context_size') else None,
                max_tokens=int(request.form['max_tokens']) if request.form.get('max_tokens') else None,
                temperature=float(request.form['temperature']) if request.form.get('temperature') else None,
                enabled=request.form.get('enabled') == 'on',
                priority=int(request.form['priority']) if request.form.get('priority') else 100,
                timeout=int(request.form['timeout']) if request.form.get('timeout') else 180,
            )
            db.add(model)
            db.commit()
        flash_message('Модель создана', 'success')
        return redirect(url_for('models_list'))

    return render_template('admin/models/form.html', user=session.get('user'), model=None, providers=providers)


@app.route('/models/edit/<uuid:model_id>', methods=['GET', 'POST'])
@admin_required
def models_edit(model_id):
    with get_db_session() as db:
        model = db.scalar(select(LLMModel).where(LLMModel.id == model_id))
        if not model:
            flash_message('Модель не найдена', 'danger')
            return redirect(url_for('models_list'))

        providers = db.scalars(select(LLMProvider).order_by(LLMProvider.name)).all()

        if request.method == 'POST':
            model.provider_id = request.form['provider_id']
            model.model_name = request.form['model_name']
            model.display_name = request.form.get('display_name')
            model.context_size = int(request.form['context_size']) if request.form.get('context_size') else None
            model.max_tokens = int(request.form['max_tokens']) if request.form.get('max_tokens') else None
            model.temperature = float(request.form['temperature']) if request.form.get('temperature') else None
            model.enabled = request.form.get('enabled') == 'on'
            model.priority = int(request.form['priority']) if request.form.get('priority') else 100
            model.timeout = int(request.form['timeout']) if request.form.get('timeout') else 180
            db.commit()
            flash_message('Модель обновлена', 'success')
            return redirect(url_for('models_list'))

    return render_template('admin/models/form.html', user=session.get('user'), model=model, providers=providers)


@app.route('/models/delete/<uuid:model_id>', methods=['POST'])
@admin_required
def models_delete(model_id):
    with get_db_session() as db:
        model = db.scalar(select(LLMModel).where(LLMModel.id == model_id))
        if model:
            db.delete(model)
            db.commit()
            flash_message('Модель удалена', 'success')
        else:
            flash_message('Модель не найдена', 'danger')
    return redirect(url_for('models_list'))


# ===================== Group Models =====================

@app.route('/group-models')
@admin_required
def group_models_list():
    with get_db_session() as db:
        relations = db.scalars(
            select(GroupModel)
            .options(joinedload(GroupModel.group))
            .options(joinedload(GroupModel.model).joinedload(LLMModel.provider))
            .join(GroupModel.group)
            .order_by(LLMGroup.name.asc(), GroupModel.is_default.desc())
        ).all()
    return render_template('admin/group_models/list.html', relations=relations, user=session.get('user'))


@app.route('/group-models/create', methods=['GET', 'POST'])
@admin_required
def group_models_create():
    with get_db_session() as db:
        groups = db.scalars(select(LLMGroup).order_by(LLMGroup.name)).all()
        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

    if request.method == 'POST':
        with get_db_session() as db:
            group_id = request.form['group_id']
            model_id = request.form['model_id']
            is_default = request.form.get('is_default') == 'on'

            if is_default:
                db.execute(
                    select(GroupModel).where(GroupModel.group_id == group_id, GroupModel.is_default == True)
                )
                existing_defaults = db.scalars(
                    select(GroupModel).where(GroupModel.group_id == group_id, GroupModel.is_default == True)
                ).all()
                for d in existing_defaults:
                    d.is_default = False

            relation = GroupModel(
                group_id=group_id,
                model_id=model_id,
                is_default=is_default,
            )
            db.add(relation)
            try:
                db.commit()
                flash_message('Связь создана', 'success')
            except Exception:
                db.rollback()
                flash_message('Такая связь уже существует', 'danger')
        return redirect(url_for('group_models_list'))

    return render_template('admin/group_models/form.html', user=session.get('user'), relation=None, groups=groups, models=models)


@app.route('/group-models/edit/<uuid:rel_id>', methods=['GET', 'POST'])
@admin_required
def group_models_edit(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(GroupModel).where(GroupModel.id == rel_id))
        if not relation:
            flash_message('Связь не найдена', 'danger')
            return redirect(url_for('group_models_list'))

        groups = db.scalars(select(LLMGroup).order_by(LLMGroup.name)).all()
        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

        if request.method == 'POST':
            with get_db_session() as db:
                group_id = request.form['group_id']
                model_id = request.form['model_id']
                is_default = request.form.get('is_default') == 'on'

                if is_default:
                    existing_defaults = db.scalars(
                        select(GroupModel).where(GroupModel.group_id == group_id, GroupModel.is_default == True)
                    ).all()
                    for d in existing_defaults:
                        if d.id != relation.id:
                            d.is_default = False

                relation.group_id = group_id
                relation.model_id = model_id
                relation.is_default = is_default
                db.commit()
                flash_message('Связь обновлена', 'success')
            return redirect(url_for('group_models_list'))

    return render_template('admin/group_models/edit.html', user=session.get('user'), relation=relation, groups=groups, models=models)


@app.route('/group-models/delete/<uuid:rel_id>', methods=['POST'])
@admin_required
def group_models_delete(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(GroupModel).where(GroupModel.id == rel_id))
        if relation:
            if relation.is_default:
                remaining = db.scalars(
                    select(GroupModel).where(GroupModel.group_id == relation.group_id, GroupModel.id != relation.id).order_by(GroupModel.relation_number)
                ).all()
                if remaining:
                    remaining[0].is_default = True
            db.delete(relation)
            db.commit()
            flash_message('Связь удалена', 'success')
        else:
            flash_message('Связь не найдена', 'danger')
    return redirect(url_for('group_models_list'))


# ===================== Fallbacks =====================

@app.route('/fallbacks')
@admin_required
def fallbacks_list():
    with get_db_session() as db:
        relations = db.scalars(
            select(LLMFallback)
            .options(
                selectinload(LLMFallback.model).selectinload(LLMModel.provider),
                selectinload(LLMFallback.model).selectinload(LLMModel.group_models).selectinload(GroupModel.group),
                selectinload(LLMFallback.fallback_model).selectinload(LLMModel.provider)
            )
            .order_by(LLMFallback.relation_number)
        ).all()
    def get_group_name(r):
        try:
            if r.model and r.model.group_models:
                return r.model.group_models[0].group.name
        except:
            pass
        return ''
    relations = sorted(relations, key=get_group_name)
    return render_template('admin/fallbacks/list.html', relations=relations, user=session.get('user'))


@app.route('/fallbacks/create', methods=['GET', 'POST'])
@admin_required
def fallbacks_create():
    with get_db_session() as db:
        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

    if request.method == 'POST':
        with get_db_session() as db:
            relation = LLMFallback(
                model_id=request.form['model_id'],
                fallback_model_id=request.form['fallback_model_id'],
                priority=int(request.form.get('priority', 1))
            )
            db.add(relation)
            try:
                db.commit()
                flash_message('Фолбэк создан', 'success')
            except Exception:
                db.rollback()
                flash_message('Такой фолбэк уже существует', 'danger')
        return redirect(url_for('fallbacks_list'))

    return render_template('admin/fallbacks/form.html', user=session.get('user'), relation=None, models=models)


@app.route('/fallbacks/edit/<uuid:rel_id>', methods=['GET', 'POST'])
@admin_required
def fallbacks_edit(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(LLMFallback).where(LLMFallback.id == rel_id))
        if not relation:
            flash_message('Фолбэк не найден', 'danger')
            return redirect(url_for('fallbacks_list'))

        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

        if request.method == 'POST':
            with get_db_session() as db:
                relation.model_id = request.form['model_id']
                relation.fallback_model_id = request.form['fallback_model_id']
                relation.priority = int(request.form.get('priority', 1))
                db.commit()
                flash_message('Фолбэк обновлён', 'success')
            return redirect(url_for('fallbacks_list'))

    return render_template('admin/fallbacks/form.html', user=session.get('user'), relation=relation, models=models)


@app.route('/fallbacks/delete/<uuid:rel_id>', methods=['POST'])
@admin_required
def fallbacks_delete(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(LLMFallback).where(LLMFallback.id == rel_id))
        if relation:
            db.delete(relation)
            db.commit()
            flash_message('Фолбэк удалён', 'success')
        else:
            flash_message('Фолбэк не найден', 'danger')
    return redirect(url_for('fallbacks_list'))


# ===================== Agent Models =====================

@app.route('/agent-models')
@admin_required
def agent_models_list():
    with get_db_session() as db:
        relations = db.scalars(
            select(AgentModel)
            .options(joinedload(AgentModel.model).joinedload(LLMModel.provider))
            .order_by(AgentModel.relation_number)
        ).all()
    return render_template('admin/agent_models/list.html', relations=relations, user=session.get('user'))


@app.route('/agent-models/create', methods=['GET', 'POST'])
@admin_required
def agent_models_create():
    with get_db_session() as db:
        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

    if request.method == 'POST':
        with get_db_session() as db:
            relation = AgentModel(
                agent_name=request.form['agent_name'],
                model_id=request.form['model_id']
            )
            db.add(relation)
            try:
                db.commit()
                flash_message('Модель агента создана', 'success')
            except Exception:
                db.rollback()
                flash_message('Такой агент уже существует', 'danger')
        return redirect(url_for('agent_models_list'))

    return render_template('admin/agent_models/form.html', user=session.get('user'), relation=None, models=models)


@app.route('/agent-models/edit/<uuid:rel_id>', methods=['GET', 'POST'])
@admin_required
def agent_models_edit(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(AgentModel).where(AgentModel.id == rel_id))
        if not relation:
            flash_message('Запись не найдена', 'danger')
            return redirect(url_for('agent_models_list'))

        models = db.scalars(
            select(LLMModel)
            .options(joinedload(LLMModel.provider))
            .order_by(LLMModel.display_name)
        ).all()

        if request.method == 'POST':
            relation.agent_name = request.form['agent_name']
            relation.model_id = request.form['model_id']
            try:
                db.commit()
                flash_message('Запись обновлена', 'success')
            except Exception:
                db.rollback()
                flash_message('Такой агент уже существует', 'danger')
            return redirect(url_for('agent_models_list'))

    return render_template('admin/agent_models/form.html', user=session.get('user'), relation=relation, models=models)


@app.route('/agent-models/delete/<uuid:rel_id>', methods=['POST'])
@admin_required
def agent_models_delete(rel_id):
    with get_db_session() as db:
        relation = db.scalar(select(AgentModel).where(AgentModel.id == rel_id))
        if relation:
            db.delete(relation)
            db.commit()
            flash_message('Запись удалена', 'success')
        else:
            flash_message('Запись не найдена', 'danger')
    return redirect(url_for('agent_models_list'))


# ===================== Research Scores =====================

@app.route('/scores')
@admin_required
def scores_list():
    with get_db_session() as db:
        scores = db.scalars(select(ResearchScore).order_by(ResearchScore.score.asc())).all()
    return render_template('admin/scores/list.html', scores=scores, user=session.get('user'))


@app.route('/scores/create', methods=['GET', 'POST'])
@admin_required
def scores_create():
    if request.method == 'POST':
        with get_db_session() as db:
            existing = db.scalar(select(ResearchScore).where(ResearchScore.score == int(request.form['score'])))
            if existing:
                existing.color = request.form['color']
            else:
                db.add(ResearchScore(score=int(request.form['score']), color=request.form['color']))
            db.commit()
        flash_message('Порог сохранён', 'success')
        return redirect(url_for('scores_list'))
    return render_template('admin/scores/form.html', user=session.get('user'), score=None)


@app.route('/scores/edit/<int:score_val>', methods=['GET', 'POST'])
@admin_required
def scores_edit(score_val):
    with get_db_session() as db:
        score = db.scalar(select(ResearchScore).where(ResearchScore.score == score_val))
        if not score:
            flash_message('Запись не найдена', 'danger')
            return redirect(url_for('scores_list'))

        if request.method == 'POST':
            score.score = int(request.form['score'])
            score.color = request.form['color']
            db.commit()
            flash_message('Порог обновлён', 'success')
            return redirect(url_for('scores_list'))

    return render_template('admin/scores/form.html', user=session.get('user'), score=score)


@app.route('/scores/delete/<int:score_val>', methods=['POST'])
@admin_required
def scores_delete(score_val):
    with get_db_session() as db:
        score = db.scalar(select(ResearchScore).where(ResearchScore.score == score_val))
        if score:
            db.delete(score)
            db.commit()
            flash_message('Порог удалён', 'success')
        else:
            flash_message('Запись не найдена', 'danger')
    return redirect(url_for('scores_list'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

import os
import json
from flask import Flask, redirect, url_for, session, render_template, request, jsonify
from authlib.integrations.flask_client import OAuth
from functools import wraps
from config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from models import User, Base

app = Flask(__name__, template_folder='templates')
app.config.from_object(Config)

DATABASE_URL = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
engine = create_engine(DATABASE_URL)

# Debug: Print all config values at startup
app.logger.info("=== Flask Config at Startup ===")
for key, value in app.config.items():
    if 'KEYCLOAK' in key or 'URL' in key:
        app.logger.info(f"{key}: {value}")

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


def role_required(roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('login'))
            user_roles = session.get('user', {}).get('roles', [])
            if not any(role in user_roles for role in roles):
                return 'Доступ запрещён. Требуется одна из ролей: ' + ', '.join(roles), 403
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@app.route('/')
def index():
    user = session.get('user')
    return render_template('index.html', user=user)


@app.route('/login')
def login():
    redirect_uri = url_for('callback', _external=True)
    
    # Debug logging
    app.logger.info(f"=== Keycloak Login Debug Info ===")
    app.logger.info(f"redirect_uri: {redirect_uri}")
    app.logger.info(f"KEYCLOAK_URL (external): {KEYCLOAK_URL}")
    app.logger.info(f"KEYCLOAK_INTERNAL_URL (internal): {KEYCLOAK_INTERNAL_URL}")
    app.logger.info(f"Request host: {request.host}")
    app.logger.info(f"Request url: {request.url}")
    
    return oauth.keycloak.authorize_redirect(redirect_uri)


@app.route('/callback')
def callback():
    try:
        token = oauth.keycloak.authorize_access_token()
        access_token = token.get('access_token', '')
        
        import base64
        
        # Extract userinfo from access token claims (since no id_token is returned)
        if access_token:
            parts = access_token.split('.')
            payload = base64.b64decode(parts[1] + '=' * (4 - len(parts[1]) % 4)).decode()
            userinfo = json.loads(payload)
        else:
            userinfo = {}
        
        # Get roles from the token
        realm_roles = []
        if 'realm_access' in userinfo and 'roles' in userinfo['realm_access']:
            realm_roles = userinfo['realm_access']['roles']
        
        keycloak_id = userinfo.get('sub', '')
        username = userinfo.get('preferred_username', '')
        email = userinfo.get('email', '')
        
        with Session(engine) as db_session:
            user = db_session.scalar(select(User).where(User.keycloak_id == keycloak_id))
            if not user:
                user = User(
                    keycloak_id=keycloak_id,
                    username=username,
                    email=email,
                    is_admin='administrator' in realm_roles,
                    is_analyst='analyst' in realm_roles,
                )
                db_session.add(user)
                db_session.commit()
        
        session['user'] = {
            'username': username,
            'email': email,
            'name': userinfo.get('name', username),
            'roles': realm_roles,
            'token': access_token,
        }

        return redirect(url_for('profile'))
    except Exception as e:
        return f'Ошибка аутентификации: {str(e)}', 400


@app.route('/profile')
@login_required
def profile():
    user = session.get('user')
    return render_template('profile.html', user=user)


@app.route('/logout')
def logout():
    id_token = session.get('user', {}).get('token', '')
    session.clear()
    
    logout_url = (
        f'{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/logout'
        f'?redirect_uri={url_for("index", _external=True)}'
    )
    return redirect(logout_url)


@app.route('/api/me')
@login_required
def api_me():
    return jsonify(session.get('user'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
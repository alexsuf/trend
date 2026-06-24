import os

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key-change-in-prod'
    KEYCLOAK_URL = os.environ.get('KEYCLOAK_URL') or 'http://auth.local'
    KEYCLOAK_INTERNAL_URL = os.environ.get('KEYCLOAK_INTERNAL_URL') or 'http://keycloak.keycloak.svc.cluster.local'
    KEYCLOAK_REALM = os.environ.get('KEYCLOAK_REALM') or 'trend'
    KEYCLOAK_CLIENT_ID = os.environ.get('KEYCLOAK_CLIENT_ID') or 'trend-web'
    KEYCLOAK_CLIENT_SECRET = os.environ.get('KEYCLOAK_CLIENT_SECRET') or 'bbWGIugaSj9ithjybqoNR5hXI9acjEel'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'postgresql://trend:secret@postgres.keycloak.svc.cluster.local:5432/trend'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

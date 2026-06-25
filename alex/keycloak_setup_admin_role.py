#!/usr/bin/env python3
"""
Create 'admin' role in Keycloak realm 'trend' and assign it to a user.
Requires: requests

Usage:
    python keycloak_setup_admin_role.py \
        --host localhost \
        --port 8080 \
        --realm trend \
        --admin-user admin \
        --admin-pass secret \
        --target-user admin
"""

import argparse
import requests
import sys
import json


def parse_args():
    parser = argparse.ArgumentParser(description='Setup admin role in Keycloak')
    parser.add_argument('--host', default='localhost', help='Keycloak host')
    parser.add_argument('--port', type=int, default=8080, help='Keycloak port')
    parser.add_argument('--realm', default='trend', help='Realm name')
    parser.add_argument('--admin-user', default='admin', help='Admin username')
    parser.add_argument('--admin-pass', default='secret', help='Admin password')
    parser.add_argument('--target-user', default='admin', help='Username to assign admin role')
    parser.add_argument('--role-name', default='administrator', help='Role name to assign (default: administrator)')
    return parser.parse_args()


def get_admin_token(args):
    url = f'http://{args.host}:{args.port}/realms/master/protocol/openid-connect/token'
    data = {
        'grant_type': 'password',
        'client_id': 'admin-cli',
        'username': args.admin_user,
        'password': args.admin_pass,
    }
    resp = requests.post(url, data=data)
    resp.raise_for_status()
    return resp.json()['access_token']


def create_role(token, args):
    role_name = args.role_name
    url = f'http://{args.host}:{args.port}/admin/realms/{args.realm}/roles'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    resp = requests.get(url + f'/{role_name}', headers=headers)
    if resp.status_code == 200:
        print(f"Role '{role_name}' already exists.")
        return

    payload = {
        'name': role_name,
        'description': f'{role_name} role for trend applications',
    }
    resp = requests.post(url, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    print(f"Role '{role_name}' created.")


def get_user_id(token, args):
    url = f'http://{args.host}:{args.port}/admin/realms/{args.realm}/users'
    headers = {'Authorization': f'Bearer {token}'}
    params = {'username': args.target_user}
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    users = resp.json()
    if not users:
        print(f"User '{args.target_user}' not found in realm '{args.realm}'")
        sys.exit(1)
    return users[0]['id']


def assign_role(token, args, user_id):
    role_name = args.role_name
    role_url = f'http://{args.host}:{args.port}/admin/realms/{args.realm}/roles/{role_name}'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    role_resp = requests.get(role_url, headers=headers)
    role_resp.raise_for_status()
    role = role_resp.json()

    mappings_url = f'http://{args.host}:{args.port}/admin/realms/{args.realm}/users/{user_id}/role-mappings/realm'
    resp = requests.get(mappings_url, headers=headers)
    resp.raise_for_status()
    existing = resp.json()
    if any(r['id'] == role['id'] for r in existing):
        print(f"User '{args.target_user}' already has role '{role_name}'.")
        return

    payload = [{'id': role['id'], 'name': role['name']}]
    resp = requests.post(mappings_url, headers=headers, data=json.dumps(payload))
    resp.raise_for_status()
    print(f"Role '{role_name}' assigned to user '{args.target_user}'.")


def main():
    args = parse_args()
    token = get_admin_token(args)
    create_role(token, args)
    user_id = get_user_id(token, args)
    assign_role(token, args, user_id)
    print("\nDone! You may need to re-login in the application.")


if __name__ == '__main__':
    main()

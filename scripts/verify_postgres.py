"""Checks TLS, runtime privileges, ciphertext and encrypted backup restoration."""
import os
import sys
import json
import subprocess
import uuid
from pathlib import Path
from cryptography.fernet import Fernet
import psycopg
from psycopg import sql
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.db import connection
from django.apps import apps
from postgres_local import load, owner_connection, backup, directory


def verify():
    root=Path(__file__).resolve().parents[1]
    config=load(root)
    with connection.cursor() as cursor:
        cursor.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
        assert cursor.fetchone()==(True,), 'TLS absent'
        cursor.execute('SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user')
        assert cursor.fetchone()==(False,False,False), 'Privilèges excessifs'
        cursor.execute("SELECT has_schema_privilege(current_user,'public','CREATE')")
        assert cursor.fetchone()==(False,), 'Création de tables autorisée au compte web'
        cursor.execute('SELECT email,first_name,last_name FROM identite_user')
        assert all(all(v.startswith('enc:v1:') for v in row) for row in cursor.fetchall()), 'Données de compte en clair'
    try:
        with psycopg.connect(host='127.0.0.1',port=config['port'],user=config['user'],password=config['password'],dbname=config['name'],sslmode='disable',connect_timeout=5):
            raise AssertionError('Connexion TCP non chiffrée acceptée')
    except psycopg.OperationalError:
        pass
    archive=backup(root)
    dump=Fernet((directory(root)/'backup.key').read_bytes()).decrypt(archive.read_bytes())
    temp_name='restore_check_'+uuid.uuid4().hex[:12]
    with owner_connection(root,'postgres') as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(temp_name)))
    try:
        subprocess.run([str(Path(config['bin'])/'pg_restore'),'-h',config['socket'],'-p',str(config['port']),'-U',config['owner'],'--exit-on-error','--no-owner','-d',temp_name],input=dump,check=True)
        with owner_connection(root,temp_name) as restored:
            for model in apps.get_models():
                with connection.cursor() as original:
                    query=sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(model._meta.db_table))
                    original.execute(query);expected=original.fetchone()[0]
                actual=restored.execute(query).fetchone()[0]
                assert expected==actual, f'Restauration incomplète : {model._meta.label}'
            for row in restored.execute('SELECT email FROM identite_user'):
                assert row[0].startswith('enc:v1:')
    finally:
        with owner_connection(root,'postgres') as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(temp_name)))
    print('Vérifié : TLS, refus du TCP en clair, droits limités, champs chiffrés et restauration de sauvegarde.')

if __name__=='__main__':verify()

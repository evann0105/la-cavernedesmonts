#!/usr/bin/env python3
"""Controlled local upgrade: preserve SQLite and activate only after validation."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
PACK=ROOT/'.postgres-upgrade'
STAGE=PACK/'app'
PRIVATE=ROOT.parent/(ROOT.name+'-data')/'postgres-private'
PYTHON=ROOT/'.venv/bin/python'


def run(args, env, cwd=STAGE, **kwargs):
    return subprocess.run([str(PYTHON),*args],cwd=cwd,env=env,check=True,**kwargs)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def stop_local_servers():
    # Stop only Django runserver listeners whose working directory is this project.
    result=subprocess.run(['lsof','-t','-iTCP:8000','-iTCP:8001','-sTCP:LISTEN'],capture_output=True,text=True)
    for value in set(result.stdout.split()):
        pid=int(value)
        command=subprocess.run(['ps','-p',value,'-o','command='],capture_output=True,text=True,check=True).stdout
        cwd=subprocess.run(['lsof','-a','-p',value,'-d','cwd','-Fn'],capture_output=True,text=True,check=True).stdout.splitlines()
        if 'manage.py' not in command or 'runserver' not in command or 'n'+str(ROOT) not in cwd:
            continue
        print(f'Arrêt du serveur Django local {pid}…',flush=True)
        os.kill(pid,signal.SIGINT)
        for _ in range(50):
            try:os.kill(pid,0)
            except ProcessLookupError:break
            time.sleep(.1)
        else:raise RuntimeError('Un serveur reste ouvert. Arrêtez-le avant de relancer la migration.')


def main():
    import environ
    from cryptography.fernet import Fernet
    PRIVATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    complete=PRIVATE/'migration-complete.json'
    if complete.exists():
        print('Migration déjà effectuée. Lancez ./start.');return
    manifest=json.loads((PACK/'manifest.json').read_text())
    for name,hashes in manifest.items():
        if digest(ROOT/name)!=hashes['before'] or digest(STAGE/name)!=hashes['after']:
            raise RuntimeError(f'Le fichier {name} a changé depuis la préparation. Régénérez le paquet avant de continuer.')
    environ.Env.read_env(ROOT/'.env')
    env=dict(os.environ,LOCAL_POSTGRES_PRIVATE=str(PRIVATE),DATABASE_CONFIG_FILE=str(PRIVATE/'connection.json'),DATA_ENCRYPTION_KEY_FILE=str(PRIVATE/'data.key'),DB_MAINTENANCE='1')
    env.pop('LEGACY_SQLITE_SOURCE',None)
    if env.get('DATABASE_URL'):
        raise RuntimeError('Cette procédure vise une base locale dédiée, pas DATABASE_URL.')
    stop_local_servers()
    print('Préparation de PostgreSQL et des clés privées…',flush=True)
    run(['scripts/postgres_local.py','--init'],env)
    # Each attempt retains its own encrypted source; never overwrite earlier backups.
    attempts=PRIVATE/'migration-attempts'
    attempts.mkdir(mode=0o700,exist_ok=True)
    source_folder=Path(tempfile.mkdtemp(prefix='attempt-',dir=attempts))
    env['MIGRATION_SOURCE_DIR']=str(source_folder)
    # Back up the actual database selected by the old Django configuration.
    code="""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django;django.setup()
from django.conf import settings
from scripts.local_database import snapshot
snapshot(settings.DATABASES['default']['NAME'],os.environ['MIGRATION_SNAPSHOT'])
"""
    fd,temporary=tempfile.mkstemp(prefix='migration-',suffix='.sqlite3',dir=PRIVATE);os.close(fd);os.unlink(temporary)
    try:
        snapshot_env=dict(os.environ,MIGRATION_SNAPSHOT=temporary)
        run(['-c',code],snapshot_env,cwd=ROOT)
        cipher=Fernet((PRIVATE/'backup.key').read_bytes().strip())
        for filename,content in [('migration-source.sqlite3.fernet',cipher.encrypt(Path(temporary).read_bytes()))]:
            fd=os.open(source_folder/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(content)
        exported=run(['manage.py','dumpdata','--all'],dict(os.environ,DATABASE_PATH=temporary),cwd=ROOT,stdout=subprocess.PIPE).stdout
        json.loads(exported)
        fd=os.open(source_folder/'migration-source.json.fernet',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(cipher.encrypt(exported))
    finally:
        Path(temporary).unlink(missing_ok=True)
    print('Import et comparaison de toutes les données…',flush=True)
    run(['scripts/transfer_to_postgres.py'],env)
    print('Tests Django sur PostgreSQL (base de test séparée)…',flush=True)
    run(['manage.py','test','--noinput'],env)
    run(['-c',"import sys;sys.path.insert(0,'scripts');from postgres_local import grant_runtime;from pathlib import Path;grant_runtime(Path.cwd())"],env)
    runtime_env=dict(env);runtime_env.pop('DB_MAINTENANCE',None)
    run(['scripts/verify_postgres.py'],runtime_env)
    run(['manage.py','check'],runtime_env)
    originals=PACK/'originals';originals.mkdir(exist_ok=True)
    applied=[]
    try:
        for name,hashes in manifest.items():
            target=ROOT/name
            if target.exists():
                original=originals/name;original.parent.mkdir(parents=True,exist_ok=True);original.write_bytes(target.read_bytes())
            target.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(dir=target.parent)
            with os.fdopen(fd,'wb') as stream:stream.write((STAGE/name).read_bytes())
            os.replace(tmp,target);applied.append(name)
        # Runtime settings use the private configuration; .env and its secrets are unchanged.
        run(['manage.py','check'],runtime_env,cwd=ROOT)
    except Exception:
        for name in reversed(applied):
            old=originals/name
            if old.exists():(ROOT/name).write_bytes(old.read_bytes())
            else:(ROOT/name).unlink()
        raise
    complete.write_text(json.dumps({'verified':True,'source_preserved':True,'files':list(manifest)},indent=2))
    print('\nMigration terminée et vérifiée. PostgreSQL est actif pour Django. Lancez ./start.',flush=True)
    print('Conservez data.key et backup.key dans un gestionnaire de secrets indépendant. L’ancienne SQLite reste conservée.',flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(f'Migration interrompue ({type(error).__name__}). Le code du site n’a pas été basculé si les validations ont échoué.',file=sys.stderr)
        print('Ne supprimez ni la base SQLite ni les clés. Faites vérifier l’étape en erreur avant de relancer.',file=sys.stderr)
        # Do not print data or credentials embedded in database exceptions.
        if isinstance(error,(RuntimeError,FileNotFoundError)):print(str(error),file=sys.stderr)
        sys.exit(1)

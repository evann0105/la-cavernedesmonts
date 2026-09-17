#!/usr/bin/env python3
"""Prepare and run the local shop; never change the production .env file."""
import argparse
import errno
import os
from pathlib import Path
import signal
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from local_database import prepare_database

ROOT = Path(__file__).resolve().parent.parent


def run(command, env):
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description='Démarrer La Caverne des Monts en local (HTTP).')
    parser.add_argument('--no-browser', action='store_true', help='Ne pas ouvrir le navigateur automatiquement.')
    parser.add_argument('--port', type=int, default=8000, help='Port local (8000 par défaut).')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Le port doit être compris entre 1024 et 65535.')
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 ou plus récent est nécessaire.')

    url = f'http://127.0.0.1:{args.port}/'
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', args.port))
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                print(f'Impossible d’ouvrir le port local : {error}', file=sys.stderr)
                return 1
            print(f'Le port {args.port} est déjà utilisé. Si le site tourne déjà, ouvrez {url}', flush=True)
            print(f'Sinon, lancez ./start --port {args.port + 1 if args.port < 65535 else 8000}. Aucun processus existant n’a été arrêté.', flush=True)
            return 1

    env = os.environ.copy()
    env.update(DEBUG='True', ALLOWED_HOSTS='localhost,127.0.0.1,[::1]', SITE_URL=url.rstrip('/'),
               DJANGO_SETTINGS_MODULE='config.settings', PYTHONUNBUFFERED='1')
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        print('Préparation de l’environnement Python…', flush=True)
        run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], env)

    # Check versions before pip: normal starts work without network access.
    dependency_check = '''
import importlib.metadata
from pathlib import Path
from pip._vendor.packaging.requirements import Requirement
for line in Path('requirements.txt').read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    requirement = Requirement(line)
    if requirement.marker and not requirement.marker.evaluate():
        continue
    version = importlib.metadata.version(requirement.name)
    if not requirement.specifier.contains(version, prereleases=True):
        raise RuntimeError('Dependency update required')
import django, environ, PIL, stripe
'''
    checked = subprocess.run([str(python), '-c', dependency_check], cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if checked.returncode:
        print('Installation des dépendances (connexion Internet nécessaire)…', flush=True)
        run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt'], env)

    env_file = ROOT / '.env'
    if not env_file.exists() and not env.get('SECRET_KEY'):
        # Exclusive create: never replace an existing operator-provided file.
        with env_file.open('x') as handle:
            os.chmod(env_file, 0o600)
            handle.write('DEBUG=True\nSECRET_KEY=' + secrets.token_urlsafe(64) + '\n')
        print('Clé locale forte créée dans .env (fichier privé).', flush=True)

    database = prepare_database(ROOT)
    env['DATABASE_PATH'] = str(database)
    print(f'Base locale protégée hors Git : {database}', flush=True)

    print('Vérification du site et préparation de la base de données…', flush=True)
    for command in ('check', 'migrate', 'import_catalogue'):
        run([str(python), 'manage.py', command], env)

    server = subprocess.Popen([str(python), 'manage.py', 'runserver', f'127.0.0.1:{args.port}'],
                              cwd=ROOT, env=env, start_new_session=True)
    try:
        # Bypass system proxies for loopback readiness checks. Do not open a failed server.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                return server.returncode or 1
            try:
                with opener.open(url, timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.2)
        else:
            print('Le serveur ne répond pas. Consultez les erreurs affichées ci-dessus.', flush=True)
            return 1
        print(f'\nSite prêt : {url}', flush=True)
        print('Utilisez http:// (sans s). Le serveur local ne fournit pas de certificat HTTPS.', flush=True)
        print('Gardez ce terminal ouvert. Pour arrêter le site : Ctrl+C.\n', flush=True)
        if not args.no_browser:
            try:
                if not webbrowser.open(url):
                    print(f'Ouvrez ce lien dans votre navigateur : {url}', flush=True)
            except webbrowser.Error:
                print(f'Ouvrez ce lien dans votre navigateur : {url}', flush=True)
        return server.wait()
    except KeyboardInterrupt:
        print('\nArrêt du site local…', flush=True)
        return 0
    finally:
        if server.poll() is None:
            os.killpg(server.pid, signal.SIGTERM)
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(server.pid, signal.SIGKILL)
                server.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except subprocess.CalledProcessError:
        print('Le démarrage a échoué. Corrigez l’erreur affichée puis relancez ./start.', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

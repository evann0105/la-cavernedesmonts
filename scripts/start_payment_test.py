#!/usr/bin/env python3
"""Launch the separate, loopback-only site for fictitious Stripe payments."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from payment_test_environment import (
    assert_database_identity, grant_runtime, launch_environment,
    prepare_environment, private_write, read_stripe_credentials, stripe_cli_path,
)

ROOT = Path(__file__).resolve().parent.parent
STRIPE_TEST_EVENTS = ','.join((
    'checkout.session.completed', 'checkout.session.async_payment_succeeded',
    'checkout.session.expired', 'refund.created', 'refund.updated', 'refund.failed',
))


def run(command, env):
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def listener_failure(logfile, offset=0):
    """Report a known error category, never a CLI log line containing secrets."""
    with Path(logfile).open('rb') as stream:
        stream.seek(offset)
        details = stream.read(65536).decode('utf-8', errors='replace').lower()
    if 'must specify events' in details:
        reason = 'La version de Stripe CLI exige une sélection explicite des événements.'
    elif any(word in details for word in ('invalid api key', 'expired api key', 'authentication', '401 unauthorized')):
        reason = 'La clé Stripe de test a été refusée ou a expiré.'
    elif any(word in details for word in ('connection refused', 'timed out', 'failed to connect', 'network is unreachable')):
        reason = 'La connexion à Stripe n’a pas pu être établie.'
    else:
        reason = 'L’écoute Stripe n’a pas confirmé son démarrage ou s’est arrêtée.'
    return reason + f' Détails conservés uniquement dans le journal privé : {logfile}'


def wait_listener_ready(process, logfile, offset=0, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(listener_failure(logfile, offset))
        with Path(logfile).open('rb') as stream:
            stream.seek(offset)
            output = stream.read(65536)
        if b'Ready!' in output:
            return
        time.sleep(0.1)
    raise RuntimeError(listener_failure(logfile, offset))


def stripe_listener(executable, private, port):
    """Capture secrets privately and keep them out of arguments and console logs."""
    credentials = read_stripe_credentials(private)
    if not credentials['secret_key']:
        raise RuntimeError('Ajoutez une clé Stripe de test au fichier privé stripe.json avant de démarrer l’écoute.')
    env = dict(os.environ, STRIPE_API_KEY=credentials['secret_key'])
    cli_config = Path(private) / 'stripe-cli.toml'
    if not cli_config.exists():
        private_write(cli_config, '')
    else:
        # Rewriting the same content validates permissions and rejects symlinks.
        private_write(cli_config, cli_config.read_text())
    command = [str(executable), '--config', str(cli_config), 'listen', '--events', STRIPE_TEST_EVENTS]
    # --print-secret does not start the listener. Both calls use the same key.
    result = subprocess.run([*command, '--print-secret'],
                            env=env, capture_output=True, text=True, timeout=30)
    secret = result.stdout.strip()
    if result.returncode or not secret.startswith('whsec_') or any(c.isspace() for c in secret):
        raise RuntimeError('La connexion Stripe de test n’a pas pu être établie. Vérifiez que la clé de test est encore valide.')
    credentials['webhook_secret'] = secret
    private_write(Path(private) / 'stripe.json', json.dumps(credentials, indent=2))
    logfile = Path(private) / 'logs' / 'stripe-listener.log'
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    offset = os.fstat(fd).st_size
    with os.fdopen(fd, 'a') as stream:
        process = subprocess.Popen(
            [*command, '--forward-to', f'http://127.0.0.1:{port}/paiement/webhook/'],
            env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
    try:
        wait_listener_ready(process, logfile, offset)
    except BaseException:
        stop(process)
        raise
    return process


def server_ready(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            return (response.status == 200 and response.geturl() == url
                    and response.headers.get('X-Caverne-Payments') == 'test'
                    and response.headers.get('X-Caverne-Local') == 'http')
    except (OSError, urllib.error.URLError):
        return False


def stop(process):
    if process is None or process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main():
    parser = argparse.ArgumentParser(description='Démarrer le site séparé de paiements fictifs Stripe.')
    parser.add_argument('--source-root', type=Path, default=Path(os.environ.get('CAVERNE_PAYMENT_TEST_SOURCE_ROOT', ROOT)),
                        help='Dossier du site qui utilise déjà le cluster PostgreSQL local.')
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--no-listener', action='store_true', help='L’écoute Stripe est lancée séparément.')
    parser.add_argument('--prepare-only', action='store_true', help='Préparer la base fictive, sans lancer de serveur.')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Le port doit être compris entre 1024 et 65535.')
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 ou plus récent est nécessaire.')
    url = f'http://127.0.0.1:{args.port}/'
    if not args.prepare_only:
        with socket.socket() as probe:
            # A freshly stopped development server can leave TCP TIME_WAIT
            # sockets behind; they must not prevent a clean restart.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', args.port))
            except OSError:
                if server_ready(url):
                    print(f'Le site de paiements fictifs est déjà ouvert : {url}', flush=True)
                    return 0
                raise RuntimeError(f'Le port {args.port} est déjà utilisé par un autre service. Aucun processus arrêté.')

    config = prepare_environment(ROOT, source_root=args.source_root)
    assert_database_identity(config)
    env = launch_environment(ROOT, port=args.port)
    python = os.environ.get('CAVERNE_PAYMENT_TEST_PYTHON') or sys.executable
    print('Préparation de la base séparée de paiements fictifs…', flush=True)
    run([python, 'manage.py', 'migrate', '--noinput'], dict(env, DB_MAINTENANCE='1'))
    grant_runtime(ROOT)
    run([python, 'manage.py', 'seed_payment_test'], env)
    run([python, 'manage.py', 'check'], env)
    if args.prepare_only:
        print('Base fictive prête. Les données de la boutique restent séparées.', flush=True)
        return 0

    server = worker = listener = None
    try:
        executable = stripe_cli_path(config['private'])
        has_key = bool(read_stripe_credentials(config['private'])['secret_key'])
        if not args.no_listener and executable and has_key:
            listener = stripe_listener(executable, config['private'], args.port)
            env = launch_environment(ROOT, port=args.port)
        elif not args.no_listener and has_key:
            print('Écoute Stripe à lancer séparément : définissez CAVERNE_STRIPE_CLI puis relancez pour recevoir les confirmations de paiement.', flush=True)
        if not has_key:
            print('Aucune clé Stripe de test configurée : le site est consultable, mais les paiements restent indisponibles.', flush=True)
        server = subprocess.Popen([python, 'manage.py', 'runserver', f'127.0.0.1:{args.port}', '--noreload'],
                                  cwd=ROOT, env=env, start_new_session=True)
        worker = subprocess.Popen([python, 'manage.py', 'reconcile_reservations', '--loop'],
                                  cwd=ROOT, env=env, start_new_session=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None:
                return server.returncode or 1
            if listener is not None and listener.poll() is not None:
                raise RuntimeError('L’écoute Stripe s’est arrêtée. Consultez son journal privé avant de tester un paiement.')
            if server_ready(url):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Le serveur de paiements fictifs n’a pas confirmé son démarrage.')
        print(f'\nPaiements fictifs uniquement : {url}', flush=True)
        print('Commandes, stocks, comptes, factures et courriels sont isolés. Ctrl+C pour arrêter.\n', flush=True)
        if not args.no_browser:
            from local_browser import open_local_browser
            open_local_browser(ROOT, url)
        while server.poll() is None:
            if worker.poll() is not None or (listener is not None and listener.poll() is not None):
                raise RuntimeError('Un service de test s’est arrêté. Relancez ./test-payments avant de continuer.')
            time.sleep(1)
        return server.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        stop(worker)
        stop(server)
        stop(listener)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print(f'Démarrage des paiements fictifs impossible : {error}', file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError:
        print('La préparation des paiements fictifs a échoué ; consultez l’erreur affichée.', file=sys.stderr)
        sys.exit(1)

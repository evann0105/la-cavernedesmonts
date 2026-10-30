"""Private, explicitly marked payment sandbox on the existing local PostgreSQL cluster.

This module never copies shop data, resets a database, or starts another cluster.
Only ``prepare_environment`` provisions the dedicated database; settings validation
is read-only and cannot silently fall back to the usual database.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import tempfile


PURPOSE = 'caverne-stripe-local-only-v1'
DB_PREFIX = 'caverne_payments_test_'


def private_directory(root, directory=None):
    root = Path(root).resolve()
    return Path(directory or os.environ.get('CAVERNE_PAYMENT_TEST_DIR') or
                root.parent / (root.name + '-payments-test-data')).absolute()


def identity(root):
    name = DB_PREFIX + hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    return name, name + '_app'


def _private(path, directory=False):
    path = Path(path)
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError('Un fichier de test doit être privé (dossier 700, fichier 600), sans lien symbolique.')
    if hasattr(os, 'getuid') and info.st_uid != os.getuid():
        raise RuntimeError('Le dossier de test doit appartenir à votre utilisateur.')
    return path


def private_write(path, value):
    """Atomic private write. Existing secrets never appear in terminal output."""
    path = Path(path)
    _private(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        _private(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path):
    try:
        value = json.loads(_private(path).read_text())
    except (ValueError, OSError) as error:
        raise RuntimeError('Configuration privée des paiements de test absente ou invalide.') from error
    if not isinstance(value, dict):
        raise RuntimeError('Configuration privée des paiements de test invalide.')
    return value


def validate_environment(root, directory=None):
    """Return a validated config without opening a connection or changing a file."""
    root = Path(root).resolve()
    private = private_directory(root, directory)
    if private.resolve().is_relative_to(root):
        raise RuntimeError('Le dossier privé de test doit rester à l’extérieur du dépôt du site.')
    _private(private, directory=True)
    marker = _read_json(private / 'environment.json')
    config = _read_json(private / 'connection.json')
    name, user = identity(root)
    if (marker.get('purpose') != PURPOSE or marker.get('root') != str(root)
            or marker.get('name') != name or marker.get('user') != user
            or len(str(marker.get('instance', ''))) != 32
            or config.get('name') != name or config.get('user') != user
            or config.get('source_name') == name
            or config.get('instance') != marker['instance']):
        raise RuntimeError('La base sélectionnée ne correspond pas à cet environnement de paiement fictif.')
    required = ('password', 'owner', 'socket', 'port', 'certificate', 'source_name')
    if any(not config.get(field) for field in required) or config['owner'] == user:
        raise RuntimeError('La connexion isolée est incomplète ou utilise un compte non limité.')
    if not Path(config['socket']).is_absolute() or not Path(config['certificate']).is_absolute():
        raise RuntimeError('La connexion doit utiliser le socket local et son certificat PostgreSQL.')
    for filename in ('django.key', 'data.key'):
        _private(private / filename)
    return {**config, 'private': private, 'marker': marker}


def read_stripe_credentials(private):
    path = Path(private) / 'stripe.json'
    credentials = _read_json(path) if path.exists() else {}
    secret = str(credentials.get('secret_key') or '').strip()
    webhook = str(credentials.get('webhook_secret') or '').strip()
    if secret and not secret.startswith(('sk_test_', 'rk_test_', 'rkcs_test_')):
        raise RuntimeError('Seules les clés Stripe de test sont autorisées ici.')
    if webhook and not webhook.startswith('whsec_'):
        raise RuntimeError('Le secret de notification Stripe est invalide.')
    return {'secret_key': secret, 'webhook_secret': webhook}


def stripe_cli_path(private):
    """Remember an explicitly configured executable without sharing CLI profiles."""
    private = Path(private)
    path = private / 'launch.json'
    configured = os.environ.get('CAVERNE_STRIPE_CLI', '').strip()
    saved = _read_json(path).get('stripe_cli', '') if path.exists() else ''
    executable = configured or saved or shutil.which('stripe')
    if not executable:
        return None
    executable = Path(executable)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError('Le chemin Stripe CLI doit être absolu et désigner un programme exécutable.')
    if configured:
        private_write(path, json.dumps({'stripe_cli': str(executable)}, indent=2))
    return executable


def launch_environment(root, port=8010, directory=None):
    if not 1024 <= int(port) <= 65535:
        raise RuntimeError('Le port de test doit être compris entre 1024 et 65535.')
    config = validate_environment(root, directory)
    private = config['private']
    credentials = read_stripe_credentials(private)
    env = dict(os.environ)
    # Empty values also prevent the regular .env file from restoring these keys.
    env.update(CAVERNE_PAYMENT_TEST='1', CAVERNE_PAYMENT_TEST_DIR=str(private),
               CAVERNE_PAYMENT_TEST_PORT=str(port),
               DJANGO_SETTINGS_MODULE='config.payment_test_settings',
               DATABASE_CONFIG_FILE=str(private / 'connection.json'),
               DATABASE_URL='', LEGACY_SQLITE_SOURCE='', DB_MAINTENANCE='',
               DATA_ENCRYPTION_KEY_FILE=str(private / 'data.key'),
               SECRET_KEY=(private / 'django.key').read_text().strip(),
               DEBUG='True', LOCAL_DEVELOPMENT_HTTP='1',
               SITE_URL=f'http://127.0.0.1:{port}',
               ALLOWED_HOSTS='127.0.0.1,localhost,[::1]',
               STRIPE_SECRET_KEY=credentials['secret_key'],
               STRIPE_WEBHOOK_SECRET=credentials['webhook_secret'],
               STRIPE_SHIPPING_RATE='', SEO_INDEXING_ENABLED='False',
               EMAIL_TEST_MODE='True', EMAIL_DELIVERY_ENABLED='True',
               EMAIL_BACKEND='courriels.backend.LocalMailboxBackend',
               PAYMENTS_ENABLED='True', PYTHONUNBUFFERED='1')
    return env


def _source_configuration(source_root):
    # Deliberately do not use DATABASE_URL, DATABASE_CONFIG_FILE, or a cluster
    # startup helper: provisioning must target this explicitly selected source.
    source_root = Path(source_root).resolve()
    path = source_root.parent / (source_root.name + '-data') / 'postgres-private' / 'connection.json'
    return _read_json(path)


def _owner_connection(config, database='postgres'):
    import psycopg
    return psycopg.connect(dbname=database, user=config['owner'],
                          host=config['socket'], port=config['port'],
                          autocommit=True, connect_timeout=5)


def _database_marker(config):
    return f"{PURPOSE}:{config['instance']}"


def assert_database_identity(config):
    """Verify the server-side marker before migrations or grants are allowed."""
    with _owner_connection(config) as connection:
        row = connection.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (config['name'],),
        ).fetchone()
    if row != (_database_marker(config),):
        raise RuntimeError('La base de test ne porte pas le marqueur attendu ; aucune modification effectuée.')


def _provision_database(config):
    from psycopg import sql
    name, user = config['name'], config['user']
    mark = _database_marker(config)
    with _owner_connection(config) as connection:
        # Serialize launches for this sandbox; never adopt an unrelated DB/role.
        connection.execute('SELECT pg_advisory_lock(hashtext(%s))', (name,))
        try:
            existing_db = connection.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s", (name,)
            ).fetchone()
            existing_role = connection.execute(
                "SELECT shobj_description(oid, 'pg_authid'), rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = %s", (user,)
            ).fetchone()
            if existing_db and existing_db != (mark,):
                raise RuntimeError('Une base différente porte déjà ce nom. Elle a été conservée sans modification.')
            if existing_role and existing_role != (mark, False, False, False, False, False):
                raise RuntimeError('Un compte PostgreSQL différent porte déjà ce nom. Il a été conservé.')
            if not existing_role:
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(user), sql.Literal(config['password'])))
                connection.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(user), sql.Literal(mark)))
            if not existing_db:
                connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name), sql.Identifier(config['owner'])))
                connection.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name), sql.Literal(mark)))
            connection.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
            connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name), sql.Identifier(user)))
        finally:
            connection.execute('SELECT pg_advisory_unlock(hashtext(%s))', (name,))
    with _owner_connection(config, name) as connection:
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')


def _allow_local_connection(config):
    """Add only this exact DB/role's TLS loopback rule, preserving existing rules."""
    with _owner_connection(config) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtext('caverne-payments-test-hba'))")
        try:
            hba = Path(connection.execute('SHOW hba_file').fetchone()[0])
            _private(hba)
            text = hba.read_text()
            comment = f"# {PURPOSE} {config['instance']}"
            rule = f"hostssl {config['name']} {config['user']} 127.0.0.1/32 scram-sha-256"
            if comment in text:
                if comment + '\n' + rule not in text:
                    raise RuntimeError('La règle locale de test a été modifiée ; vérifiez sa configuration.')
                return
            private_write(hba, comment + '\n' + rule + '\n' + text)
            if not connection.execute('SELECT pg_reload_conf()').fetchone()[0]:
                raise RuntimeError('PostgreSQL n’a pas rechargé la règle locale de test.')
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext('caverne-payments-test-hba'))")


def prepare_environment(root, source_root=None, directory=None):
    """Provision an empty test DB, never import or alter the source shop's data."""
    from cryptography.fernet import Fernet
    root = Path(root).resolve()
    private = private_directory(root, directory)
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private(private, directory=True)
    source = _source_configuration(source_root or root)
    if not (private / 'environment.json').exists():
        if any(private.iterdir()):
            raise RuntimeError('Le dossier de test contient des fichiers sans marqueur. Aucun fichier remplacé.')
        name, user = identity(root)
        if name == source.get('name') or user in (source.get('user'), source.get('owner')):
            raise RuntimeError('La base et le compte de test doivent être différents de ceux de la boutique.')
        marker = {'purpose': PURPOSE, 'root': str(root), 'name': name,
                  'user': user, 'instance': secrets.token_hex(16)}
        config = {key: source[key] for key in ('owner', 'socket', 'port', 'certificate')}
        config.update(name=name, user=user, password=secrets.token_urlsafe(48),
                      source_name=source['name'], instance=marker['instance'])
        # Marker last: an interrupted setup is never accepted as ready.
        private_write(private / 'connection.json', json.dumps(config, indent=2))
        private_write(private / 'django.key', secrets.token_urlsafe(64) + '\n')
        private_write(private / 'data.key', Fernet.generate_key().decode() + '\n')
        private_write(private / 'environment.json', json.dumps(marker, indent=2))
    config = validate_environment(root, private)
    # Follow an existing cluster port change, but never silently switch clusters.
    if any(config[key] != source[key] for key in ('owner', 'socket', 'certificate')) or config['source_name'] != source['name']:
        raise RuntimeError('Cet environnement dépend d’un autre cluster local. Aucune base modifiée.')
    if config['port'] != source['port']:
        stored = _read_json(private / 'connection.json')
        stored['port'] = source['port']
        private_write(private / 'connection.json', json.dumps(stored, indent=2))
        config = validate_environment(root, private)
    _provision_database(config)
    assert_database_identity(config)
    _allow_local_connection(config)
    for name in ('emails', 'media', 'logs'):
        (private / name).mkdir(exist_ok=True, mode=0o700)
        _private(private / name, directory=True)
    return config


def grant_runtime(root, directory=None):
    from psycopg import sql
    config = validate_environment(root, directory)
    assert_database_identity(config)
    with _owner_connection(config, config['name']) as connection:
        role = sql.Identifier(config['user'])
        for statement in (
            'GRANT USAGE ON SCHEMA public TO {}',
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}',
            'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}',
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}',
            'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}',
        ):
            connection.execute(sql.SQL(statement).format(role))

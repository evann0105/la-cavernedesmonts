"""Dedicated local PostgreSQL instance, private socket and verified TLS for Django."""
import getpass
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import shutil
import subprocess
from datetime import datetime, timedelta, timezone


def private_write(path, content):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)


def pg_bin():
    value = os.environ.get('POSTGRES_BIN')
    if value:
        return Path(value)
    executable = shutil.which('pg_ctl')
    if executable:
        return Path(executable).parent
    for prefix in ('/opt/homebrew/opt', '/usr/local/opt'):
        for version in (18, 17, 16, 15):
            directory = Path(prefix) / f'postgresql@{version}' / 'bin'
            if (directory / 'pg_ctl').exists():
                return directory
    raise RuntimeError('PostgreSQL est requis. Installez une version maintenue puis relancez.')


def directory(root):
    return Path(os.environ.get('LOCAL_POSTGRES_PRIVATE', str(Path(root).parent / (Path(root).name + '-data') / 'postgres-private')))


def load(root):
    return json.loads((directory(root) / 'connection.json').read_text())


def atomic_private_write(path, content):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def available_port(preferred):
    # Bind only to loopback; never stop another service to reclaim its port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', preferred))
        except OSError as error:
            import errno
            if error.errno != errno.EADDRINUSE:
                raise
            probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def ensure_started(root):
    config = load(root)
    binary = Path(config['bin'])
    cluster = config['cluster']
    pidfile = Path(cluster)/'postmaster.pid'
    if pidfile.exists():
        # pg_ctl status may be unable to inspect processes started outside this session.
        # Verify this cluster over its private socket before touching its configuration.
        import psycopg
        try:
            active_port = int(pidfile.read_text().splitlines()[3])
            with psycopg.connect(dbname='postgres', user=config['owner'],
                                  host=config['socket'], port=active_port,
                                  connect_timeout=5) as connection:
                data_directory = connection.execute('SHOW data_directory').fetchone()[0]
                if Path(data_directory).resolve() != Path(cluster).resolve():
                    raise RuntimeError('Le serveur actif ne correspond pas au cluster attendu.')
        except (psycopg.Error, ValueError, IndexError) as error:
            raise RuntimeError('Un fichier de serveur PostgreSQL existe mais sa connexion privée échoue. Aucun redémarrage ni changement de port effectué.') from error
        if config['port'] != active_port:
            config['port'] = active_port
            atomic_private_write(directory(root)/'connection.json', json.dumps(config, indent=2))
        port = active_port
    else:
        port = available_port(config['port'])
        if port != config['port']:
            print(f"Port PostgreSQL {config['port']} occupé ; utilisation du port local {port}.")
            config['port'] = port
            atomic_private_write(directory(root)/'connection.json', json.dumps(config, indent=2))
    conf = Path(cluster)/'postgresql.conf'
    content = conf.read_text()
    marker = '# Port managed by the local launcher'
    content = content.split('\n' + marker)[0]
    atomic_private_write(conf, content + f'\n{marker}\nport = {port}\n')
    if not pidfile.exists():
        subprocess.run([str(binary/'pg_ctl'), '-D', cluster, '-l', str(Path(cluster).parent/'postgres.log'), '-w', 'start'], check=True)
    return config


def owner_connection(root, database=None):
    import psycopg
    config = ensure_started(root)
    return psycopg.connect(dbname=database or config['name'], user=config['owner'], host=config['socket'], port=config['port'], autocommit=True)


def grant_runtime(root):
    from psycopg import sql
    config = load(root)
    with owner_connection(root) as connection:
        user = sql.Identifier(config['user'])
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(user))
        connection.execute(sql.SQL('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}').format(user))
        connection.execute(sql.SQL('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(user))
        connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}').format(user))
        connection.execute(sql.SQL('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}').format(user))


def initialize(root):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from cryptography.fernet import Fernet
    from psycopg import sql
    folder = directory(root)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (folder/'connection.json').exists():
        config = ensure_started(root)
        provision_database(root, config)
        return config
    binary = pg_bin()
    cluster = folder/'cluster'
    socket_dir = folder.parent/'pgsocket'
    socket_dir.mkdir(mode=0o700, exist_ok=True)
    config = {'name':'caverne', 'owner':'caverne_owner', 'user':'caverne_app', 'password':secrets.token_urlsafe(48), 'port':55432, 'socket':str(socket_dir), 'cluster':str(cluster), 'bin':str(binary), 'certificate':str(folder/'server.crt')}
    if cluster.exists():
        raise RuntimeError('Cluster déjà présent sans configuration ; arrêt pour éviter un écrasement.')
    subprocess.run([str(binary/'initdb'), '-D', str(cluster), '--username',config['owner'], '--auth-local=peer','--auth-host=scram-sha-256','--encoding=UTF8','--locale=C'],check=True,stdout=subprocess.DEVNULL)
    private_key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=5)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=825)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False).add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True).sign(private_key,hashes.SHA256()))
    private_write(folder/'server.key',private_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode())
    private_write(folder/'server.crt',cert.public_bytes(serialization.Encoding.PEM).decode())
    for keyname in ('data.key','backup.key'):
        if not (folder/keyname).exists():private_write(folder/keyname,Fernet.generate_key().decode())
    with (cluster/'postgresql.conf').open('a') as f:
        f.write(f"\nlisten_addresses = '127.0.0.1'\nport = 55432\nunix_socket_directories = '{socket_dir}'\nunix_socket_permissions = 0700\nssl = on\nssl_cert_file = '{folder/'server.crt'}'\nssl_key_file = '{folder/'server.key'}'\nssl_min_protocol_version = 'TLSv1.2'\npassword_encryption = 'scram-sha-256'\nlog_statement = 'none'\nlog_min_error_statement = 'panic'\n")
    (cluster/'pg_ident.conf').write_text(f'local_owner {getpass.getuser()} caverne_owner\n')
    (cluster/'pg_hba.conf').write_text('local all caverne_owner peer map=local_owner\nlocal all all reject\nhostssl caverne caverne_app 127.0.0.1/32 scram-sha-256\nhost all all 0.0.0.0/0 reject\nhost all all ::/0 reject\n')
    private_write(folder/'connection.json',json.dumps(config,indent=2))
    config = ensure_started(root)
    provision_database(root, config)
    return config


def provision_database(root, config):
    from psycopg import sql
    with owner_connection(root,'postgres') as connection:
        role_exists = connection.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', (config['user'],)).fetchone()
        if not role_exists:
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION').format(sql.Identifier(config['user']),sql.Literal(config['password'])))
        database_exists = connection.execute('SELECT 1 FROM pg_database WHERE datname = %s', (config['name'],)).fetchone()
        if not database_exists:
            connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(config['name']),sql.Identifier(config['owner'])))
        connection.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(config['name'])))
        connection.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(config['name']),sql.Identifier(config['user'])))
    with owner_connection(root) as connection:
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
    return config


def backup(root):
    from cryptography.fernet import Fernet
    config=ensure_started(root)
    result=subprocess.run([str(Path(config['bin'])/'pg_dump'),'-h',config['socket'],'-p',str(config['port']),'-U',config['owner'],'-Fc',config['name']],check=True,stdout=subprocess.PIPE)
    folder=directory(root);destination=folder/'backups';destination.mkdir(mode=0o700,exist_ok=True)
    path=destination/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.dump.fernet')
    private_write(path,Fernet((folder/'backup.key').read_bytes().strip()).encrypt(result.stdout).decode())
    return path

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    import sys
    if '--init' in sys.argv:initialize(root);print('PostgreSQL local préparé.')
    else:print('Sauvegarde chiffrée :',backup(root))

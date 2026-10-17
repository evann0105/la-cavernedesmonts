"""Database configuration with certificate verification enforced for TCP."""
import json
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured


def database_config(base_dir, env):
    # Explicit legacy source, only for export/recovery; never an automatic fallback.
    legacy = os.environ.get('LEGACY_SQLITE_SOURCE')
    if legacy:
        return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': legacy}
    config_file = Path(env('DATABASE_CONFIG_FILE', default=str(base_dir.parent / (base_dir.name + '-data') / 'postgres-private' / 'connection.json')))
    if env('DATABASE_URL', default=''):
        db = env.db('DATABASE_URL')
        if db['ENGINE'] != 'django.db.backends.postgresql':
            raise ImproperlyConfigured('DATABASE_URL doit désigner PostgreSQL.')
        db.setdefault('OPTIONS', {})
        db['OPTIONS'].update(sslmode='verify-full', sslrootcert=env('DATABASE_SSL_ROOT_CERT'))
        db['OPTIONS']['connect_timeout'] = 5
        return db
    if not config_file.exists():
        raise ImproperlyConfigured('PostgreSQL non configuré. Consultez docs/postgresql.md.')
    config = json.loads(config_file.read_text())
    if os.environ.get('DB_MAINTENANCE') == '1':
        return {'ENGINE':'django.db.backends.postgresql','NAME':config['name'],'USER':config['owner'],'HOST':config['socket'],'PORT':config['port']}
    return {'ENGINE':'django.db.backends.postgresql','NAME':config['name'],'USER':config['user'],'PASSWORD':config['password'],'HOST':'127.0.0.1','PORT':config['port'],'CONN_MAX_AGE':60,'CONN_HEALTH_CHECKS':True,'OPTIONS':{'sslmode':'verify-full','sslrootcert':config['certificate'],'connect_timeout':5}}

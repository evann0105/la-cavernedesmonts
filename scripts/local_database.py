"""Keep development data outside Git and snapshot SQLite safely, including WAL."""
from datetime import datetime, timezone
from pathlib import Path
import os
import sqlite3
import tempfile


def snapshot(source, destination):
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=destination.parent, suffix='.sqlite3')
    os.close(fd)
    try:
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(temporary) as copy:
                original.backup(copy)
                if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('La sauvegarde SQLite est invalide.')
        # Exclusive publication: never overwrite a database or previous backup.
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)


def prepare_database(root):
    root = Path(root).resolve()
    directory = root.parent / (root.name + '-data')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / 'db.sqlite3'
    legacy = root / 'db.sqlite3'
    if not target.exists() and legacy.exists() and legacy.stat().st_size:
        snapshot(legacy, target)
    if target.exists() and target.stat().st_size:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        snapshot(target, directory / 'backups' / f'db-{stamp}.sqlite3')
    return target

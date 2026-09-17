from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.local_database import prepare_database


class LocalDatabaseTests(TestCase):
    def test_migration_backup_and_branch_switch_preserve_data(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'shop'
            root.mkdir()
            legacy = root / 'db.sqlite3'
            with sqlite3.connect(legacy) as db:
                db.execute('CREATE TABLE example (value TEXT)')
                db.execute("INSERT INTO example VALUES ('original')")
            target = prepare_database(root)
            self.assertFalse(target.is_relative_to(root))
            with sqlite3.connect(target) as db:
                db.execute("UPDATE example SET value = 'new data'")
            # An old branch replaces its tracked database; protected data survives.
            legacy.write_bytes(b'old branch content')
            self.assertEqual(prepare_database(root), target)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'new data')
            backups = sorted((target.parent / 'backups').glob('*.sqlite3'))
            self.assertEqual(len(backups), 2)
            with sqlite3.connect(backups[-1]) as db:
                self.assertEqual(db.execute('SELECT value FROM example').fetchone()[0], 'new data')

    def test_new_install_creates_external_directory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'shop'
            root.mkdir()
            target = prepare_database(root)
            self.assertTrue(target.parent.is_dir())
            self.assertFalse(target.exists())

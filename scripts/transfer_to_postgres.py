"""Import only into the new, dedicated database, then compare every source field."""
import json
import os
import sys
from pathlib import Path
from cryptography.fernet import Fernet
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.apps import apps
from django.core import serializers
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connection, transaction
from psycopg import sql
from config.encrypted_fields import email_digest


def transfer():
    folder=Path(os.environ['LOCAL_POSTGRES_PRIVATE'])
    source_folder=Path(os.environ.get('MIGRATION_SOURCE_DIR',str(folder)))
    source=json.loads(Fernet((folder/'backup.key').read_bytes()).decrypt((source_folder/'migration-source.json.fernet').read_bytes()))
    for item in source:
        if item['model']=='auth.user':item['model']='identite.user'
        if item['model']=='contenttypes.contenttype' and item['fields']=={'app_label':'auth','model':'user'}:
            item['fields']['app_label']='identite'
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
        if cursor.fetchone()[0]:
            raise RuntimeError('La base cible doit être vide. Aucun écrasement automatique autorisé.')
    call_command('migrate',verbosity=0)
    with transaction.atomic():
        tables=[t for t in connection.introspection.table_names() if t!='django_migrations']
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL('TRUNCATE {} RESTART IDENTITY CASCADE').format(sql.SQL(',').join(map(sql.Identifier,tables))))
        for item in serializers.deserialize('json',json.dumps(source)):
            if item.object._meta.label_lower=='identite.user':item.object.email_hash=email_digest(item.object.email)
            item.save()
        connection.check_constraints()
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(no_style(),apps.get_models()):cursor.execute(statement)
        for expected in source:
            model=apps.get_model(expected['model'])
            obj=model._default_manager.get(pk=expected['pk'])
            actual=json.loads(serializers.serialize('json',[obj]))[0]
            for name,value in expected['fields'].items():
                got=actual['fields'][name]
                if isinstance(value,list) and all(isinstance(v,(str,int)) for v in value):
                    value=sorted(value);got=sorted(got)
                if got!=value:
                    raise RuntimeError(f'Vérification échouée : {expected["model"]}.{name} (contenu masqué).')
        for label in {item['model'] for item in source}:
            if apps.get_model(label).objects.count()!=sum(item['model']==label for item in source):
                raise RuntimeError(f'Nombre de lignes incohérent : {label}')
    print(f'Transfert vérifié champ par champ : {len(source)} objets. Mots de passe hachés conservés.')

if __name__=='__main__':transfer()

from django.db import migrations


def manager_group(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    Group = apps.get_model('auth', 'Group')
    content_type, _ = ContentType.objects.get_or_create(app_label='core', model='product')
    permission, _ = Permission.objects.get_or_create(content_type=content_type, codename='manage_catalog', defaults={'name':'Gérer le catalogue depuis la boutique'})
    group, _ = Group.objects.get_or_create(name='Gestion du catalogue')
    group.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('core', '0005_alter_product_options_and_more'), ('auth', '0012_alter_user_first_name_max_length')]
    operations = [migrations.RunPython(manager_group, migrations.RunPython.noop)]

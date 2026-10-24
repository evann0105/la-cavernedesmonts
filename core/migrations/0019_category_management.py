from django.db import migrations, models


def preserve_current_menu(apps, schema_editor):
    Category = apps.get_model('core', 'Category')
    categories = list(Category.objects.using(schema_editor.connection.alias).order_by('name', 'pk'))
    defaults = {'femmes': (10, 'Femme'), 'hommes': (20, 'Homme'),
                'enfants': (30, 'Enfant'), 'accessoires': (40, 'Petits bonheurs')}
    extra_position = 50
    for category in categories:
        if category.slug in defaults:
            category.position, category.menu_label = defaults[category.slug]
            category.show_in_menu = True
        else:
            category.position = extra_position
            extra_position += 10
        category.save(update_fields=['position', 'menu_label', 'show_in_menu'])


class Migration(migrations.Migration):
    dependencies = [('core', '0018_product_duplicate_of')]
    operations = [
        migrations.AddField(model_name='category', name='menu_label',
            field=models.CharField('Libellé dans le menu', blank=True, max_length=40)),
        migrations.AddField(model_name='category', name='show_in_menu',
            field=models.BooleanField('Afficher dans le menu', default=False)),
        migrations.AddField(model_name='category', name='position',
            field=models.PositiveIntegerField('Ordre', default=100)),
        migrations.AddField(model_name='category', name='revision',
            field=models.PositiveIntegerField(default=1, editable=False)),
        migrations.AlterModelOptions(name='category',
            options={'ordering': ['position', 'name', 'pk'], 'verbose_name_plural': 'Categories'}),
        migrations.RunPython(preserve_current_menu, migrations.RunPython.noop),
    ]

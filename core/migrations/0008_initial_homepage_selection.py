from django.db import migrations


def preserve_cards(apps, schema_editor):
    Product = apps.get_model('core', 'Product')
    Selection = apps.get_model('core', 'HomepageSelection')
    for slot, slug in [('femmes', 'veste-tri-matieres-femme-blanche-capuche'), ('hommes', 'blouson-softshell-homme-impermeable'), ('enfants', 'enfant-74-polaire-garcon-ceven')]:
        product = Product.objects.filter(slug=slug).first()
        if product:
            Selection.objects.get_or_create(slot=slot, defaults={'product':product})


class Migration(migrations.Migration):
    dependencies = [('core', '0007_homepageselection')]
    operations = [migrations.RunPython(preserve_cards, migrations.RunPython.noop)]

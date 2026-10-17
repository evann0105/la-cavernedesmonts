from django.db import migrations
from django.db.models import Q


def move_baby_products(apps, schema_editor):
    Product = apps.get_model('core', 'Product')
    Category = apps.get_model('core', 'Category')
    alias = schema_editor.connection.alias
    gifts, _ = Category.objects.using(alias).get_or_create(slug='accessoires', defaults={'name':'Petits bonheurs'})
    children = Category.objects.using(alias).filter(slug='enfants').first()
    baby = Product.objects.using(alias).filter(
        Q(category__slug__in=['accessoires-bebe', 'polaires-bebe']) |
        Q(collections__slug__in=['accessoires-bebe', 'polaires-bebe']) |
        Q(name__icontains='bébé') | Q(name__icontains='bebe') | Q(name__icontains='chaussette')
    ).distinct()
    for product in baby.iterator():
        if product.category_id and product.category.slug in ('accessoires-bebe', 'polaires-bebe'):
            product.collections.add(product.category_id)
        product.category_id = gifts.pk
        product.save(using=alias, update_fields=['category'])
        if children:
            product.collections.remove(children.pk)


class Migration(migrations.Migration):
    dependencies = [('core', '0012_product_color_photos')]
    # Intentional merchandising change: reversing must not guess old categories.
    operations = [migrations.RunPython(move_baby_products, migrations.RunPython.noop)]

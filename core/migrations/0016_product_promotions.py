from django.db import migrations, models
import django.db.models.deletion


def observe_existing_prices(apps, schema_editor):
    """Observe today's database; this does not claim prices existed 30 days ago."""
    Product = apps.get_model('core', 'Product')
    Variant = apps.get_model('stocks', 'Variant')
    Observation = apps.get_model('core', 'PriceObservation')
    using = schema_editor.connection.alias
    base = dict(Product.objects.using(using).values_list('pk', 'price'))
    Observation.objects.using(using).bulk_create([
        Observation(product_id=pk, price=price) for pk, price in base.items() if price > 0
    ])
    Observation.objects.using(using).bulk_create([
        Observation(product_id=variant.product_id, variant_id=variant.pk,
                    price=variant.price if variant.price is not None else base[variant.product_id])
        for variant in Variant.objects.using(using).all()
        if (variant.price if variant.price is not None else base[variant.product_id]) > 0
    ])


class Migration(migrations.Migration):
    dependencies = [('core', '0015_product_main_color'), ('stocks', '0004_variant_price')]
    operations = [
        migrations.CreateModel(
            name='ProductPromotion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('starts_at', models.DateTimeField(verbose_name='Début')),
                ('ends_at', models.DateTimeField(verbose_name='Fin')),
                ('kind', models.CharField(choices=[('percent', 'Pourcentage'), ('amount', 'Remise en euros'), ('fixed', 'Prix remisé')], max_length=10, verbose_name='Type de réduction')),
                ('value', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Valeur')),
                ('enabled', models.BooleanField(default=True, verbose_name='Activée')),
                ('all_variants', models.BooleanField(default=True, verbose_name='Toutes les tailles et couleurs')),
                ('reference_prices', models.JSONField(blank=True, default=dict, verbose_name='Prix de référence confirmés')),
                ('reference_confirmed', models.BooleanField(default=False, verbose_name='Prix les plus bas des 30 jours précédents vérifiés')),
                ('cancelled_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Annulée le')),
                ('revision', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='promotions', to='core.product')),
                ('targets', models.ManyToManyField(blank=True, related_name='promotions', to='stocks.variant')),
            ],
            options={
                'ordering': ['starts_at', 'pk'],
                'indexes': [models.Index(fields=['product', 'starts_at', 'ends_at'], name='promotion_product_dates')],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('ends_at__gt', models.F('starts_at'))), name='promotion_positive_period'),
                    models.CheckConstraint(condition=models.Q(('value__gt', 0)), name='promotion_positive_value'),
                    models.CheckConstraint(condition=models.Q(models.Q(('kind', 'percent'), _negated=True), ('value__lt', 100), _connector='OR'), name='promotion_percent_below_100'),
                    models.CheckConstraint(condition=models.Q(('kind__in', ['percent', 'amount', 'fixed'])), name='promotion_valid_kind'),
                ],
            },
        ),
        migrations.CreateModel(
            name='PriceObservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('observed_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='price_observations', to='core.product')),
                ('variant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='price_observations', to='stocks.variant')),
            ],
            options={
                'ordering': ['observed_at', 'pk'],
                'indexes': [models.Index(fields=['product', 'variant', 'observed_at'], name='observed_product_variant_date')],
                'constraints': [models.CheckConstraint(condition=models.Q(('price__gt', 0)), name='observed_price_positive')],
            },
        ),
        migrations.RunPython(observe_existing_prices, migrations.RunPython.noop),
    ]

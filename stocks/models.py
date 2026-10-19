from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower


class Variant(models.Model):
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='stock_variants')
    size = models.CharField('Taille', max_length=50)
    color = models.CharField('Couleur', max_length=80)
    reference = models.CharField('Référence interne', max_length=80, blank=True)
    quantity = models.PositiveIntegerField('Quantité en stock', null=True, blank=True)
    price = models.DecimalField('Prix de cette taille et couleur (€)', max_digits=10, decimal_places=2, null=True, blank=True, default=None, validators=[MinValueValidator(Decimal('0.01'))])
    reserved = models.PositiveIntegerField(default=0, editable=False)
    revision = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['size', 'color', 'pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(reserved=0) | (models.Q(quantity__isnull=False) & models.Q(quantity__gte=models.F('reserved'))), name='reserved_within_stock'),
            models.UniqueConstraint('product', Lower('size'), Lower('color'), name='unique_stock_combination'),
            models.CheckConstraint(condition=models.Q(price__isnull=True) | models.Q(price__gt=0), name='positive_variant_price'),
            models.CheckConstraint(condition=models.Q(quantity__gte=0) | models.Q(quantity__isnull=True), name='nonnegative_stock'),
        ]

    @property
    def regular_price(self):
        return self.price if self.price is not None else self.product.price

    @property
    def effective_price(self):
        from core.pricing import price_quote
        return price_quote(self.product, self).current

    def save(self, *args, **kwargs):
        from django.db import transaction
        price_may_change = kwargs.get('update_fields') is None or 'price' in kwargs['update_fields']
        previous = None
        if self.pk and price_may_change:
            previous = type(self).objects.filter(pk=self.pk).values('price', 'product_id').first()
        price_changed = price_may_change and (previous is None or previous['price'] != self.price or previous['product_id'] != self.product_id)
        with transaction.atomic(using=kwargs.get('using') or self._state.db or 'default'):
            super().save(*args, **kwargs)
            if price_changed:
                from core.pricing import record_variant_price
                record_variant_price(self)
        if price_changed:
            self.__dict__.pop('_pricing_context', None)
            if 'product' in self._state.fields_cache:
                self.product.__dict__.pop('_pricing_context', None)
                self.product.__dict__.pop('_pricing_at', None)

    @property
    def available(self):
        return max(0, (self.quantity or 0) - self.reserved)

    def __str__(self):
        return f'{self.product} — {self.size} · {self.color}'


class Reservation(models.Model):
    order = models.ForeignKey('paiement.Order', on_delete=models.PROTECT, related_name='reservations')
    variant = models.ForeignKey(Variant, on_delete=models.PROTECT, related_name='reservations')
    quantity = models.PositiveIntegerField()
    state = models.CharField(max_length=12, default='held', choices=[('held','Réservé'),('sold','Vendu'),('released','Libéré')])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['order','variant'],name='one_order_variant_hold'),models.CheckConstraint(condition=models.Q(quantity__gt=0),name='positive_reservation')]


class VariantLabelCorrection(models.Model):
    """An intentional label correction; purchase snapshots remain untouched."""
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name='label_corrections')
    product_identifier = models.PositiveBigIntegerField()
    previous_size = models.CharField(max_length=50)
    previous_color = models.CharField(max_length=80)
    corrected_size = models.CharField(max_length=50)
    corrected_color = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

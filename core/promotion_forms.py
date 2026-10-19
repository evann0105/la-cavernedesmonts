"""Plan catalog reductions against confirmed 30-day reference prices."""
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import ProductPromotion
from .pricing import reference_price_limit, regular_price
from .product_options import color_key

PARIS = ZoneInfo('Europe/Paris')
CENT = Decimal('0.01')


class ParisDateTimeField(forms.DateTimeField):
    def to_python(self, value):
        # Django rejects missing/ambiguous hours on the daylight-saving changes.
        with timezone.override(PARIS):
            return super().to_python(value)


class ProductPromotionForm(forms.Form):
    start_mode = forms.ChoiceField(label='Quand commencer ?', choices=[
        ('scheduled', 'À la date et à l’heure choisies'), ('immediate', 'Dès maintenant'),
    ], widget=forms.RadioSelect, initial='scheduled')
    starts_at = ParisDateTimeField(label='Début de la réduction', required=False,
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))
    ends_at = ParisDateTimeField(label='Fin de la réduction',
        widget=forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}))
    scope = forms.ChoiceField(label='Sur quels articles ?', choices=[
        ('all', 'Toutes les tailles et couleurs du produit'), ('color', 'Une couleur, toutes ses tailles'),
        ('variants', 'Certaines tailles et couleurs'),
    ], initial='all', widget=forms.RadioSelect)
    color = forms.ChoiceField(label='Couleur concernée', required=False)
    targets = forms.MultipleChoiceField(label='Tailles et couleurs concernées', required=False,
        widget=forms.CheckboxSelectMultiple)
    kind = forms.ChoiceField(label='Type de réduction', choices=[
        ('percent', 'Pourcentage de réduction (%)'), ('amount', 'Montant à déduire (€)'),
        ('fixed', 'Prix final pendant la période (€)'),
    ])
    value = forms.DecimalField(label='Valeur de la réduction', decimal_places=2, max_digits=10,
        min_value=CENT, widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'inputmode': 'decimal'}))
    reference_confirmed = forms.BooleanField(label='Je confirme que ces prix de référence correspondent aux prix les plus bas pratiqués pendant les 30 jours précédant la réduction.')

    def __init__(self, *args, product, variants=None, promotions=None, now=None, **kwargs):
        self.product = product
        self.variants = list(variants if variants is not None else product.stock_variants.all())
        self.promotions = list(promotions if promotions is not None else ProductPromotion.objects.filter(product=product).prefetch_related('targets'))
        self.now = now or timezone.now()
        super().__init__(*args, **kwargs)
        self.target_objects = {str(variant.pk): variant for variant in self.variants} or {'product': None}
        groups = {}
        self.color_targets = {}
        for variant in self.variants:
            key = color_key(variant.color)
            groups.setdefault(key, {'name': variant.color or 'Coloris unique', 'choices': []})['choices'].append((str(variant.pk), f'{variant.size} · {regular_price(product, variant):.2f} €'))
            self.color_targets.setdefault(key, set()).add(str(variant.pk))
        self.fields['color'].choices = [('', 'Choisir une couleur')] + [(key, group['name']) for key, group in groups.items()]
        self.fields['targets'].choices = [(group['name'], group['choices']) for group in groups.values()]
        if not self.variants:
            self.fields['scope'].choices = [('all', 'Tout le produit')]
        self.reference_rows = []
        for key, variant in self.target_objects.items():
            field_name = f'reference_{key}'
            base = regular_price(product, variant)
            known = reference_price_limit(product, variant, starts_at=self.now)
            self.fields[field_name] = forms.DecimalField(label=f'Prix de référence : {variant.size} · {variant.color}' if variant else 'Prix de référence du produit', required=False,
                min_value=CENT, decimal_places=2, max_digits=10, initial=min(base, known) if known is not None else base,
                widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'inputmode': 'decimal', 'data-reference-price': ''}))
            self.reference_rows.append({'key': key, 'color': color_key(variant.color) if variant else '',
                'label': f'{variant.size} · {variant.color}' if variant else product.name,
                'regular_price': base, 'known_reference': known, 'field': self[field_name]})

    def clean(self):
        data = super().clean()
        start = data.get('starts_at')
        if data.get('start_mode') == 'immediate':
            start = self.now
        elif start is None:
            self.add_error('starts_at', 'Choisissez une date et une heure de début.')
        elif start < self.now:
            self.add_error('starts_at', 'Choisissez un début à venir, ou « Dès maintenant ».')
        end = data.get('ends_at')
        data['starts_at'] = start
        if start and end and end <= start:
            self.add_error('ends_at', 'La fin doit être postérieure au début de la réduction.')
        scope = data.get('scope')
        if scope == 'all':
            keys = set(self.target_objects)
        elif scope == 'color':
            keys = self.color_targets.get(data.get('color'), set())
            if not keys:
                self.add_error('color', 'Choisissez une couleur existante de ce produit.')
        else:
            keys = set(data.get('targets', []))
            if not keys:
                self.add_error('targets', 'Choisissez au moins une taille et une couleur.')
        data['target_keys'] = keys
        data['all_variants'] = scope == 'all'
        kind, value = data.get('kind'), data.get('value')
        if kind == 'percent' and value is not None and value >= 100:
            self.add_error('value', 'Le pourcentage doit être inférieur à 100 %.')
        references = {}
        for key in sorted(keys):
            field = f'reference_{key}'
            reference = data.get(field)
            if reference is None:
                if field not in self.errors:
                    self.add_error(field, 'Renseignez le prix de référence de cette taille et couleur.')
                continue
            variant = self.target_objects[key]
            base = regular_price(self.product, variant)
            known = reference_price_limit(self.product, variant, starts_at=start or self.now)
            if reference > base or (known is not None and reference > known):
                self.add_error(field, 'Ce prix ne peut pas dépasser le prix habituel ni le prix le plus bas déjà enregistré sur les 30 jours précédents.')
                continue
            references[key] = str(reference)
            if value is None or kind not in ('percent', 'amount', 'fixed'):
                continue
            reduced = (reference * (1 - value / 100) if kind == 'percent' else reference - value if kind == 'amount' else value).quantize(CENT, rounding=ROUND_HALF_UP)
            if reduced <= 0 or reduced >= reference or reduced >= base:
                self.add_error('value', f'La réduction doit donner un prix positif, inférieur au prix de référence et au prix habituel pour chaque taille et couleur concernée ({reference:.2f} € de référence).')
                break
        data['reference_prices'] = references
        if start and end and end > start and keys:
            for promotion in self.promotions:
                if not promotion.enabled or promotion.starts_at >= end or promotion.ends_at <= start:
                    continue
                existing = set(self.target_objects) if promotion.all_variants else {str(target.pk) for target in promotion.targets.all()}
                if scope == 'all' or promotion.all_variants or keys & existing:
                    raise ValidationError('Une autre réduction couvre déjà cette période pour au moins une taille ou couleur concernée. Annulez-la ou choisissez une autre période.')
        return data

    def save(self):
        if not self.is_valid():
            raise ValueError('Cannot save an invalid promotion form.')
        data = self.cleaned_data
        promotion = ProductPromotion.objects.create(product=self.product, starts_at=data['starts_at'], ends_at=data['ends_at'],
            kind=data['kind'], value=data['value'], enabled=True, all_variants=data['all_variants'],
            reference_prices=data['reference_prices'], reference_confirmed=True)
        if not data['all_variants']:
            promotion.targets.set([self.target_objects[key] for key in data['target_keys']])
        return promotion

from decimal import Decimal
from urllib.parse import urlsplit
from django import forms

from .shipping_countries import COUNTRY_CHOICES
from .models import ShippingCountry


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


def _day_field(label):
    return forms.IntegerField(label=label, min_value=0, max_value=60, required=False,
                              widget=forms.NumberInput(attrs={'min': 0, 'max': 60}))


def _clean_days(form, minimum, maximum):
    data = form.cleaned_data
    start, end = data.get(minimum), data.get(maximum)
    if (start is None) != (end is None):
        form.add_error(minimum if start is None else maximum, 'Renseignez les deux délais, ou laissez-les tous les deux vides pour préparer un brouillon.')
    elif start is not None and end is not None and start > end:
        form.add_error(maximum, 'Le délai maximum doit être supérieur ou égal au délai minimum.')


class ShippingPolicyForm(RevisionForm):
    enabled = forms.BooleanField(label='Autoriser la livraison en ligne', required=False,
                                help_text='Les paiements restent soumis aux autres vérifications avant l’ouverture des ventes.')
    free_shipping_enabled = forms.BooleanField(label='Proposer la livraison offerte à partir d’un montant', required=False)
    free_shipping_threshold = forms.DecimalField(label='Montant d’articles pour la livraison offerte (€)',
        min_value=Decimal('0'), max_digits=10, decimal_places=2,
        help_text='Seuil calculé sur les articles avant code client. Zéro : sans minimum. Les zones peuvent être exclues de cette offre.')
    preparation_days_min = _day_field('Préparation minimum (jours ouvrés)')
    preparation_days_max = _day_field('Préparation maximum (jours ouvrés)')
    public_notice = forms.CharField(label='Information complémentaire pour les clients', required=False,
        max_length=1500, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Facultatif. Cette précision accompagne les conditions de livraison.')

    def clean(self):
        data = super().clean()
        _clean_days(self, 'preparation_days_min', 'preparation_days_max')
        return data


class ShippingCarrierForm(RevisionForm):
    carrier_name = forms.CharField(label='Nom du transporteur', required=False, max_length=150,
        help_text='Laissez vide tant que le transporteur n’a pas été choisi. La livraison restera à compléter.')
    service_name = forms.CharField(label='Nom du service affiché aux clients', max_length=150,
                                   help_text='Exemple : Livraison standard.')
    carrier_website = forms.URLField(label='Site du transporteur', required=False, max_length=500,
        help_text='Lien HTTPS facultatif pour accéder manuellement au site du transporteur. Il ne connecte aucun compte et ne crée pas d’étiquette.')

    def clean_carrier_website(self):
        value = self.cleaned_data.get('carrier_website', '')
        if value:
            parts = urlsplit(value)
            if parts.scheme != 'https' or parts.username is not None or parts.password is not None:
                raise forms.ValidationError('Utilisez une adresse HTTPS sans identifiant ni mot de passe.')
        return value


class ShippingZoneForm(RevisionForm):
    configuration_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    title = forms.CharField(label='Nom de la zone', max_length=150,
                            help_text='Regroupez les pays qui partagent le même tarif et les mêmes délais.')
    price = forms.DecimalField(label='Frais de livraison standard (€)', min_value=Decimal('0'),
                               max_digits=10, decimal_places=2, required=False,
                               help_text='Vide : tarif à compléter. Zéro : livraison gratuite pour cette zone.')
    free_shipping_eligible = forms.BooleanField(label='Appliquer la livraison offerte à partir du seuil', required=False,
        help_text='Si cette case est décochée, le tarif de cette zone reste applicable même au-dessus du seuil général.')
    delivery_days_min = _day_field('Acheminement minimum (jours ouvrés)')
    delivery_days_max = _day_field('Acheminement maximum (jours ouvrés)')
    active = forms.BooleanField(label='Activer cette zone pour les clients', required=False,
                               help_text='Une zone active doit avoir un tarif, des délais et au moins un pays.')
    countries = forms.MultipleChoiceField(label='Pays de cette zone', required=False,
        choices=sorted(COUNTRY_CHOICES, key=lambda choice: choice[1].casefold()),
        widget=forms.CheckboxSelectMultiple,
        help_text='Un pays ne peut appartenir qu’à une seule zone, même si celle-ci est désactivée.')

    def __init__(self, *args, zone=None, **kwargs):
        self.zone = zone
        super().__init__(*args, **kwargs)

    def clean_countries(self):
        countries = self.cleaned_data['countries']
        if len(countries) != len(set(countries)):
            raise forms.ValidationError('Sélectionnez chaque pays une seule fois.')
        used = ShippingCountry.objects.filter(code__in=countries)
        if self.zone is not None:
            used = used.exclude(zone_id=self.zone.pk)
        conflicts = list(used.values_list('code', flat=True))
        if conflicts:
            labels = dict(COUNTRY_CHOICES)
            raise forms.ValidationError('Ces pays appartiennent déjà à une autre zone : ' + ', '.join(labels[code] for code in conflicts) + '.')
        return sorted(countries)

    def clean(self):
        data = super().clean()
        _clean_days(self, 'delivery_days_min', 'delivery_days_max')
        if data.get('active'):
            for field in ('price', 'delivery_days_min', 'delivery_days_max'):
                if data.get(field) is None and field not in self.errors:
                    self.add_error(field, 'Complétez cette information avant d’activer la zone.')
            if not data.get('countries') and 'countries' not in self.errors:
                self.add_error('countries', 'Choisissez au moins un pays avant d’activer la zone.')
        return data

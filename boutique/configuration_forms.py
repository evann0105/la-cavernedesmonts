"""Small, separate edit forms for public contacts and business information."""
import re

from django import forms
from .configuration import normalize_legal_form


class RevisionForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class ContactsForm(RevisionForm):
    phone = forms.CharField(label='Téléphone de la boutique', max_length=64,
                           help_text='Exemple : 04 50 40 46 17. Il sera aussi utilisé pour les boutons d’appel.')
    email = forms.EmailField(label='Adresse e-mail publique', max_length=254,
                            help_text='Adresse affichée aux clients. Ce réglage ne modifie pas les identifiants ni le service d’envoi des e-mails.')

    def clean_phone(self):
        value = self.cleaned_data['phone'].strip()
        digits = re.sub(r'\D', '', value)
        if not re.fullmatch(r'\+?[0-9 .()/\-]+', value) or not 6 <= len(digits) <= 15:
            raise forms.ValidationError('Renseignez un seul numéro de téléphone valide, avec des chiffres et éventuellement +, espaces, points, tirets ou parenthèses.')
        return value

    def clean_email(self):
        return self.cleaned_data['email'].strip().lower()


class StoreAddressForm(RevisionForm):
    store_address_line1 = forms.CharField(label='Numéro et rue', max_length=255,
                                         help_text='Exemple : 225 rue des Monts-Jura.')
    store_address_line2 = forms.CharField(label='Complément d’adresse', max_length=255, required=False,
                                         help_text='Facultatif : résidence, bâtiment ou étage.')
    store_postal_code = forms.CharField(label='Code postal', max_length=20)
    store_city = forms.CharField(label='Ville', max_length=120)
    store_country = forms.CharField(label='Pays', max_length=80)

    def clean(self):
        data = super().clean()
        if str(data.get('store_country', '')).casefold() in ('fr', 'france'):
            postal_code = data.get('store_postal_code')
            if postal_code and not re.fullmatch(r'[0-9]{5}', postal_code):
                self.add_error('store_postal_code', 'En France, indiquez les cinq chiffres du code postal.')
            data['store_country'] = 'France'
        return data


class LinkedAddressForm(RevisionForm):
    address_field = ''
    same_field = ''

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # When linked, ignore even an overlong textarea submitted by an old
        # tab or a hand-built request. The saved independent address is kept.
        if self.is_bound and self.fields[self.same_field].widget.value_from_datadict(
                self.data, self.files, self.add_prefix(self.same_field)):
            self.fields[self.address_field].disabled = True

    def clean(self):
        data = super().clean()
        if not data.get(self.same_field) and not data.get(self.address_field):
            self.add_error(self.address_field, 'Renseignez cette adresse ou utilisez celle du magasin.')
        return data


class ReturnsAddressForm(LinkedAddressForm):
    address_field = 'return_address'
    same_field = 'returns_same_as_store'
    returns_same_as_store = forms.BooleanField(label='Utiliser la même adresse que le magasin', required=False,
        help_text='Les retours suivront automatiquement les changements d’adresse du magasin. Votre dernière adresse distincte reste conservée.')
    return_address = forms.CharField(label='Adresse de retour distincte', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 5}),
        help_text='À compléter si les colis doivent revenir à une autre adresse. Si la case ci-dessus est cochée, ce texte est ignoré.')


class CompanyForm(LinkedAddressForm):
    address_field = 'business_address'
    same_field = 'business_same_as_store'
    business_name = forms.CharField(label='Nom juridique de l’entreprise', max_length=255,
                                    help_text='Exemple : Élodie FOLOPPE. Ce nom identifie le vendeur sur les nouveaux documents.')
    legal_form = forms.CharField(label='Forme juridique', max_length=80,
                                 help_text='Exemple : EI. Les libellés « Entrepreneur individuel » sont automatiquement abrégés en EI.')
    siren = forms.CharField(label='SIREN', max_length=20, help_text='Les neuf chiffres du numéro SIREN.')
    siret = forms.CharField(label='SIRET', max_length=25, help_text='Les quatorze chiffres du SIRET, commençant par le SIREN.')
    registration = forms.CharField(label='Immatriculation', max_length=255,
                                   help_text='Exemple : 525 114 914 RCS Bourg-en-Bresse.')
    publisher = forms.CharField(label='Responsable de publication', max_length=255, required=False,
                                help_text='Nom de la personne responsable du site, à confirmer avec la gérante.')
    business_same_as_store = forms.BooleanField(label='L’adresse professionnelle est celle du magasin', required=False,
        help_text='Si cette case est cochée, les changements d’adresse du magasin seront repris sur les nouveaux documents. Les documents déjà enregistrés restent conservés.')
    business_address = forms.CharField(label='Adresse professionnelle distincte', max_length=2000, required=False,
        widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Adresse juridique du vendeur. À compléter uniquement si elle diffère de celle du magasin.')

    def clean_legal_form(self):
        return normalize_legal_form(self.cleaned_data['legal_form'])

    def clean_siren(self):
        value = re.sub(r'\s', '', self.cleaned_data['siren'])
        if not re.fullmatch(r'[0-9]{9}', value):
            raise forms.ValidationError('Le SIREN doit comporter exactement neuf chiffres.')
        return value

    def clean_siret(self):
        value = re.sub(r'\s', '', self.cleaned_data['siret'])
        if not re.fullmatch(r'[0-9]{14}', value):
            raise forms.ValidationError('Le SIRET doit comporter exactement quatorze chiffres.')
        return value

    def clean(self):
        data = super().clean()
        if data.get('siren') and data.get('siret') and not data['siret'].startswith(data['siren']):
            self.add_error('siret', 'Le SIRET doit commencer par les neuf chiffres du SIREN renseigné.')
        return data

from decimal import Decimal
import uuid

from django import forms
from django.contrib.auth import get_user_model
from config.encrypted_fields import email_digest

from .models import CustomerBenefit


class BenefitForm(forms.ModelForm):
    revision = forms.CharField(required=False, widget=forms.HiddenInput)
    creation_key = forms.UUIDField(required=False, widget=forms.HiddenInput)
    reassign_customer = forms.BooleanField(required=False, label='Réattribuer au compte correspondant à cette adresse', help_text='À utiliser si le client a changé de compte. Cette action est conservée dans l’historique.')
    justification = forms.CharField(
        required=False, max_length=2000, label='Justification visible par le client',
        help_text='Expliquez votre geste au client. Ce message apparaît dans son espace et dans son e-mail ; ce n’est pas une note interne.',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Par exemple : Pour vous remercier de votre fidélité à la boutique.'}),
    )
    notify = forms.BooleanField(
        required=False, initial=True, label='Informer le client par e-mail',
        help_text='Le message indique le code, ses conditions et le lien vers « Mes avoirs ».',
    )

    class Meta:
        model = CustomerBenefit
        fields = [
            'recipient_email', 'label', 'justification', 'kind', 'value', 'minimum_subtotal',
            'starts_at', 'ends_at', 'active',
        ]
        labels = {
            'recipient_email': 'Adresse e-mail du client',
            'label': 'Intitulé visible par le client',
            'kind': 'Type d’avantage',
            'value': 'Montant (€) ou pourcentage (%)',
            'minimum_subtotal': 'Minimum d’achats (€)',
            'starts_at': 'Disponible à partir du',
            'ends_at': 'Valable jusqu’au',
            'active': 'Activer ce code',
        }
        help_texts = {
            'recipient_email': 'Réservé au client destinataire. Utilisez l’adresse e-mail de son compte.',
            'label': 'Par exemple : Merci pour votre fidélité ou Avoir commercial.',
            'value': 'Exemple : 15 pour une réduction de 15 % ou un montant de 15 €.',
            'minimum_subtotal': 'Montant des articles après les réductions du catalogue, hors livraison.',
            'starts_at': 'Facultatif. Sans date de début, le code est disponible immédiatement.',
            'ends_at': 'Facultatif. Le code expire exactement à la date et à l’heure indiquées.',
        }
        widgets = {
            'recipient_email': forms.EmailInput(attrs={'autocomplete': 'off', 'maxlength': 254}),
            'label': forms.TextInput(attrs={'placeholder': 'Un geste de la boutique'}),
            'value': forms.NumberInput(attrs={'min': '0.01', 'step': '0.01', 'inputmode': 'decimal'}),
            'minimum_subtotal': forms.NumberInput(attrs={'min': '0', 'step': '0.01', 'inputmode': 'decimal'}),
            'starts_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'ends_at': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('starts_at', 'ends_at'):
            self.fields[name].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S']
        self.fields['minimum_subtotal'].initial = Decimal('0.00')
        self.fields['active'].initial = True
        self.fields['creation_key'].initial = uuid.uuid4()
        if self.instance.pk:
            self.fields['notify'].initial = False
            self.fields['revision'].initial = self.instance.updated_at.isoformat()

    def clean_recipient_email(self):
        return self.cleaned_data['recipient_email'].strip().lower()

    def clean(self):
        data = super().clean()
        if self.instance.pk and data.get('revision') != self.instance.updated_at.isoformat():
            raise forms.ValidationError('Cet avoir a été modifié entre-temps. Rechargez la page avant de recommencer.')
        if data.get('recipient_email'):
            self.instance.user = get_user_model().objects.filter(email_hash=email_digest(data['recipient_email'])).first()
        start, end = data.get('starts_at'), data.get('ends_at')
        if start and end and end <= start:
            self.add_error('ends_at', 'La fin doit être postérieure au début.')
        if data.get('kind') == 'percent' and data.get('value', 0) > 100:
            self.add_error('value', 'Le pourcentage doit être compris entre 0,01 et 100.')
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.user = get_user_model().objects.filter(
            email__iexact=instance.recipient_email, is_active=True,
        ).first()
        if commit:
            instance.save()
        return instance

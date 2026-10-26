import uuid

from django import forms

from .models import SupportCase


class SupportCaseForm(forms.Form):
    creation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    customer_name = forms.CharField(label='Nom du client', max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'name'}))
    customer_email = forms.EmailField(label='Adresse e-mail', max_length=254, required=False,
        widget=forms.EmailInput(attrs={'autocomplete': 'email'}))
    phone = forms.CharField(label='Téléphone', max_length=40, required=False,
        widget=forms.TextInput(attrs={'type': 'tel', 'autocomplete': 'tel'}),
        help_text='Un téléphone ou un e-mail permet de retrouver et de recontacter le client.')
    subject = forms.CharField(label='Objet de la demande', max_length=160,
        widget=forms.TextInput(attrs={'placeholder': 'Ex. : vérifier la livraison de la commande'}))
    description = forms.CharField(label='Demande du client', max_length=5000,
        widget=forms.Textarea(attrs={'rows': 6}),
        help_text='Notez les faits utiles et ce que le client attend de la boutique.')
    channel = forms.ChoiceField(label='Demande reçue par', choices=SupportCase.CHANNELS, initial='phone')
    category = forms.ChoiceField(label='Type de demande', choices=SupportCase.CATEGORIES, initial='other')
    priority = forms.ChoiceField(label='Priorité', choices=SupportCase.PRIORITIES, initial='normal')
    due_date = forms.DateField(label='À reprendre le', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d', '%d/%m/%Y'],
        help_text='Facultatif. Cette date vous aide à suivre les demandes à rappeler.')

    def clean_customer_email(self):
        return self.cleaned_data.get('customer_email', '').strip().lower()

    def clean_phone(self):
        value = self.cleaned_data.get('phone', '').strip()
        if value:
            digits = ''.join(char for char in value if char.isdigit())
            if not 6 <= len(digits) <= 20 or any(char not in '0123456789+(). /-' for char in value):
                raise forms.ValidationError('Renseignez un numéro de téléphone valide, avec son indicatif si nécessaire.')
        return value

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get('customer_email') or cleaned.get('phone')):
            raise forms.ValidationError('Renseignez un e-mail ou un téléphone pour retrouver le client.')
        return cleaned


class SupportUpdateForm(forms.Form):
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    state = forms.ChoiceField(label='État du dossier', choices=SupportCase.STATES)
    priority = forms.ChoiceField(label='Priorité', choices=SupportCase.PRIORITIES)
    due_date = forms.DateField(label='À reprendre le', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d', '%d/%m/%Y'])
    note = forms.CharField(label='Note de suivi', required=False, max_length=5000,
        widget=forms.Textarea(attrs={'rows': 5}),
        help_text='Cette note reste dans le dossier de la boutique. Décrivez la solution avant de passer le dossier à « Résolu ».')

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance = instance
        if instance is not None:
            self.initial.update({name: getattr(instance, name) for name in ('revision', 'state', 'priority', 'due_date')})

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get('state') == 'resolved' and
                (self.instance is None or self.instance.state != 'resolved') and not cleaned.get('note')):
            self.add_error('note', 'Indiquez la solution apportée avant de clôturer le dossier.')
        return cleaned

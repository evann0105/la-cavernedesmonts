from django import forms

from .models import HistoryVersion


class HistoryFilterForm(forms.Form):
    q = forms.CharField(label='Rechercher', required=False, max_length=100,
        widget=forms.TextInput(attrs={'type': 'search', 'placeholder': 'Produit, modification…'}))
    scope = forms.ChoiceField(label='Contenu', required=False,
        choices=(('', 'Tous les contenus'), ('product', 'Fiches produits'), ('homepage', 'Accueil')))
    source = forms.ChoiceField(label='Type de modification', required=False,
        choices=(('', 'Toutes les actions'), *HistoryVersion._meta.get_field('source').choices))
    actor = forms.ChoiceField(label='Personne', required=False, choices=())
    since = forms.DateField(label='À partir du', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}))
    until = forms.DateField(label='Jusqu’au', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, actors=(), fixed_scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['actor'].choices = [('', 'Toutes les personnes'), *((name, name) for name in actors)]
        if fixed_scope:
            self.fields['scope'].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('since') and cleaned.get('until') and cleaned['since'] > cleaned['until']:
            self.add_error('until', 'La date de fin doit être après la date de début.')
        return cleaned


class RestoreForm(forms.Form):
    confirmation = forms.CharField(max_length=4000, widget=forms.HiddenInput())
    confirmed = forms.BooleanField(
        label='J’ai vérifié les différences et je souhaite rétablir cette version.',
        error_messages={'required': 'Cochez la confirmation après avoir vérifié les différences.'},
    )

"""Seller confirmation for a complete cancellation before shipment."""
from django import forms
from .reasons import (INTERNAL_NOTE_MAX_LENGTH, PUBLIC_REASON_MAX_LENGTH,
                      REASON_CHOICES, REASON_TEXTS, public_reason)


class ReasonSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        option['attrs']['data-reason-text'] = REASON_TEXTS.get(str(value), '')
        return option


class CancellationForm(forms.Form):
    preview_token = forms.CharField(widget=forms.HiddenInput, max_length=8192)
    reason_code = forms.ChoiceField(label='Motif de l’annulation', required=False,
        choices=(('', 'Choisir un motif'), *REASON_CHOICES), widget=ReasonSelect,
        help_text='Choisissez une suggestion, puis adaptez le message si nécessaire.')
    reason = forms.CharField(
        label='Motif de l’annulation (visible par le client)',
        max_length=PUBLIC_REASON_MAX_LENGTH, required=False,
        widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Expliquez simplement la raison de l’annulation. Ce texte sera visible dans le suivi du client.',
        error_messages={'required': 'Indiquez le motif à communiquer au client.'},
    )
    internal_note = forms.CharField(label='Note interne à la boutique', required=False,
        max_length=INTERNAL_NOTE_MAX_LENGTH, widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Facultative. Réservée à la boutique ; elle n’est pas communiquée au client.')
    confirmed = forms.BooleanField(
        label='Je confirme que le colis n’a pas été remis au transporteur et que toute la commande doit être annulée.',
        error_messages={'required': 'Confirmez que le colis n’est pas parti et que toute la commande doit être annulée.'},
    )

    def __init__(self, *args, is_demo=False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_demo:
            self.fields['confirmed'].label = 'Je souhaite simuler l’annulation de toute cette commande avant son expédition.'

    def clean(self):
        cleaned = super().clean()
        if 'reason' in cleaned and 'reason_code' in cleaned:
            try:
                cleaned['reason'] = public_reason(cleaned['reason'], cleaned['reason_code'])
            except ValueError as error:
                self.add_error('reason', str(error))
        return cleaned


class CancellationInternalNoteForm(forms.Form):
    internal_note = forms.CharField(label='Note interne à la boutique', required=False,
        max_length=INTERNAL_NOTE_MAX_LENGTH, widget=forms.Textarea(attrs={'rows': 4}),
        help_text='Réservée à la boutique ; elle n’est pas communiquée au client.')
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0, max_value=2147483647)

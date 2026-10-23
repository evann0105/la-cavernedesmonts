"""Explicit seller confirmations; shipment rules remain in the domain service."""
from django import forms


class ActionForm(forms.Form):
    action_name = ''
    action = forms.ChoiceField(widget=forms.HiddenInput)
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.is_demo = kwargs.pop('is_demo', False)
        super().__init__(*args, **kwargs)
        self.fields['action'].choices = [(self.action_name, self.action_name)]
        self.initial['action'] = self.action_name


class ReadyForm(ActionForm):
    action_name = 'ready'
    articles_checked = forms.BooleanField(label='Les articles, tailles, couleurs et quantités sont vérifiés.')
    address_checked = forms.BooleanField(label='Le destinataire et l’adresse de livraison sont vérifiés.')
    packing_checked = forms.BooleanField(label='L’emballage et le bon de livraison sont vérifiés.')
    notify_customer = forms.BooleanField(label='Prévenir le client que sa commande est prête', required=False,
        help_text='Facultatif. Ce message ne confirme pas encore une expédition.')


class ReopenForm(ActionForm):
    action_name = 'reopen'
    note = forms.CharField(label='Motif de la reprise de préparation', required=False, max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}), help_text='Facultatif. Ce commentaire reste dans l’historique de la boutique.')


class TrackingFields(ActionForm):
    carrier = forms.ChoiceField(label='Transporteur', choices=[('colissimo', 'Colissimo'), ('other', 'Autre transporteur')])
    carrier_name = forms.CharField(label='Nom du transporteur si vous choisissez « Autre »', required=False, max_length=150)
    tracking_number = forms.CharField(label='Numéro de suivi du colis', max_length=120,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'spellcheck': 'false'}),
        help_text='Recopiez le numéro de l’étiquette fournie par le transporteur. Aucun numéro de suivi n’est créé par le site.')
    notify_customer = forms.BooleanField(label='Prévenir le client à l’adresse e-mail de sa commande', initial=True,
        help_text='Obligatoire. Le message reprend le transporteur et le suivi ; son statut d’envoi reste consultable ici.')

    def clean(self):
        data = super().clean()
        if data.get('carrier') == 'other' and not data.get('carrier_name'):
            self.add_error('carrier_name', 'Renseignez le nom du transporteur choisi.')
        return data


class ShipForm(TrackingFields):
    action_name = 'ship'
    handover_confirmed = forms.BooleanField(label='Je confirme que le colis a été remis au transporteur.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_demo:
            self.fields['handover_confirmed'].label = 'Je souhaite simuler la remise du colis au transporteur.'


class UpdateTrackingForm(TrackingFields):
    action_name = 'update_tracking'
    note = forms.CharField(label='Motif de la correction', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Obligatoire. Le motif reste dans l’historique de la boutique ; le client reçoit les nouvelles informations de suivi.')


class ResendNoticeForm(ActionForm):
    action_name = 'resend_notice'


FORM_CLASSES = {
    'ready': ReadyForm, 'reopen': ReopenForm, 'ship': ShipForm,
    'update_tracking': UpdateTrackingForm, 'resend_notice': ResendNoticeForm,
}

from django import forms


class ExportTransmissionForm(forms.Form):
    action = forms.ChoiceField(choices=(('sent', 'Transmis'), ('reopen', 'À transmettre')), widget=forms.HiddenInput)
    expected_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    transmitted_on = forms.DateField(label='Date de transmission', required=False,
        input_formats=['%Y-%m-%d'], widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    recipient = forms.CharField(label='Destinataire', max_length=200, required=False,
        help_text='Le nom du comptable ou son adresse e-mail, si vous souhaitez le préciser.')
    note = forms.CharField(label='Commentaire', max_length=1000, required=False,
        widget=forms.Textarea(attrs={'rows': 3}), help_text='Par exemple : envoyé avec les relevés de paiement.')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'sent' and not cleaned.get('transmitted_on'):
            self.add_error('transmitted_on', 'Renseignez le jour où vous avez transmis ce fichier.')
        return cleaned

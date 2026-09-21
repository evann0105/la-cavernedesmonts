from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from .models import Address
from connexion.security import check_current_password

class AddressForm(forms.ModelForm):
    class Meta:
        model = Address
        exclude = ['user']
        labels = {key:_(value) for key,value in {'label':'Nom de cette adresse','recipient':'Destinataire','line1':'Adresse','line2':'Complément','postal_code':'Code postal','city':'Ville','country':'Pays'}.items()}

class PersonalForm(forms.ModelForm):
    current_password = forms.CharField(label=_('Mot de passe actuel'), widget=forms.PasswordInput, help_text=_('Pour confirmer la modification de vos informations.'))
    email = forms.EmailField(label=_('Adresse e-mail'))
    class Meta:
        model = get_user_model()
        fields = ['first_name', 'last_name', 'email']
        labels = {'first_name':_('Prénom'), 'last_name':_('Nom')}
    def clean_current_password(self):
        value = self.cleaned_data['current_password']
        if not check_current_password(self.instance, value):
            raise forms.ValidationError(_('Mot de passe incorrect.'))
        return value
    def clean_email(self):
        value = self.cleaned_data['email'].strip().lower()
        if get_user_model().objects.filter(email__iexact=value).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('Cette adresse e-mail est déjà utilisée.'))
        return value

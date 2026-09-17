from django.utils.translation import gettext_lazy as _
from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth.models import User


class SignupForm(UserCreationForm):
    email = forms.EmailField(label=_('Adresse e-mail'))

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(_('Cette adresse e-mail est déjà utilisée.'))
        return email


class BoutiqueAuthenticationForm(AuthenticationForm):
    username = forms.CharField(label=_('Adresse e-mail ou nom d’utilisateur'), widget=forms.TextInput(attrs={'autocomplete': 'username', 'autofocus': True}), max_length=254)

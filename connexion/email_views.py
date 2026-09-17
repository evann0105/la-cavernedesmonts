from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render,redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.utils.translation import gettext_lazy as _
from .email_verification import email_verified,email_ready,send_verification,token_matches,confirm_token


class VerificationEmailForm(forms.Form):
    email=forms.EmailField(label=_('Adresse e-mail'))
    password=forms.CharField(label=_('Mot de passe actuel'),widget=forms.PasswordInput)
    def __init__(self,*args,user,**kwargs):
        self.user=user
        super().__init__(*args,**kwargs)
    def clean(self):
        data=super().clean()
        if not self.user.check_password(data.get('password','')):
            raise forms.ValidationError(_('Mot de passe incorrect.'))
        email=data.get('email','').strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError(_('Cette adresse e-mail est déjà utilisée.'))
        data['email']=email
        return data


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def verify_email(request):
    verified=email_verified(request.user)
    form=VerificationEmailForm(request.POST if request.method=='POST' and not request.user.email else None,user=request.user)
    if request.method=='POST' and not verified:
        if not request.user.email:
            if not form.is_valid():
                return render(request,'connexion/verify_email.html',{'form':form,'ready':email_ready()})
            request.user.email=form.cleaned_data['email'];request.user.save(update_fields=['email'])
        result=send_verification(request.user)
        labels={'sent':_('Le lien de confirmation a été envoyé. Consultez votre boîte mail et vos courriers indésirables.'),'limited':_('Veuillez patienter avant de demander un nouveau lien. Maximum cinq demandes par heure.'),'unavailable':_('L’envoi des e-mails est momentanément indisponible. Contactez la boutique.'),'verified':_('Votre adresse e-mail est déjà confirmée.')}
        messages.info(request,labels[result])
        return redirect('connexion:verify_email')
    return render(request,'connexion/verify_email.html',{'form':form,'verified':verified,'ready':email_ready()})


@never_cache
@login_required
@require_http_methods(['GET','POST'])
def confirm_email(request,token):
    valid=token_matches(request.user,token)
    if request.method=='POST' and valid:
        if confirm_token(request.user,token):
            messages.success(request,_('Adresse e-mail confirmée. Bienvenue dans votre compte !'))
            return redirect('espace:account')
        valid=False
    response=render(request,'connexion/confirm_email.html',{'valid':valid},status=200 if valid else 400)
    response['Referrer-Policy']='no-referrer'
    return response

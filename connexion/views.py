from urllib.parse import urlsplit
from django.contrib.auth import login, logout, get_user_model
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.cache import never_cache
from django.contrib.auth.views import LoginView
from .email_verification import email_access_allowed, send_verification
from .forms import SignupForm, BoutiqueAuthenticationForm
from .security import allow_signup


class SecureLoginView(LoginView):
    authentication_form = BoutiqueAuthenticationForm
    template_name = 'connexion/login.html'

    def get_success_url(self):
        target = self.get_redirect_url()
        if not email_access_allowed(self.request.user):
            # Preserve the emailed link after signing in on another browser.
            if target and urlsplit(target).path.startswith('/accounts/confirmer-email/'):
                return target
            send_verification(self.request.user)
            return reverse('connexion:verify_email')
        return self.get_redirect_url() or reverse('espace:account')


login_view = SecureLoginView.as_view()


@never_cache
@require_http_methods(['GET', 'POST'])
def signup_view(request):
    if request.user.is_authenticated:
        return redirect('accueil:home')
    if request.method == 'POST' and not allow_signup(request):
        response = render(request, 'connexion/locked.html', {'minutes': 30}, status=429)
        response['Retry-After'] = '1800'
        return response
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            if get_user_model().objects.filter(email__iexact=form.cleaned_data['email']).exists():
                form.add_error('email', _('Cette adresse e-mail est déjà utilisée.'))
            elif get_user_model().objects.filter(username=form.cleaned_data['username']).exists():
                form.add_error('username', _('Ce nom d’utilisateur est déjà utilisé.'))
            else:
                raise
            return render(request, 'connexion/signup.html', {'form': form})
        login(request, user, backend='connexion.backends.EmailOrUsernameBackend')
        if email_access_allowed(user):
            return redirect('espace:account')
        send_verification(user)
        return redirect('connexion:verify_email')
    return render(request, 'connexion/signup.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('accueil:home')

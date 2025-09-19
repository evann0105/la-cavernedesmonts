from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.GET.get('next') or reverse('accueil:home'))

    ctx = {"error": None}

    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        password = request.POST.get('password', '')
        user = None
        # Try username
        user = authenticate(request, username=identifier, password=password)
        if user is None:
            # Try email
            from django.contrib.auth.models import User
            try:
                u = User.objects.get(email__iexact=identifier)
                user = authenticate(request, username=u.username, password=password)
            except User.DoesNotExist:
                user = None
        if user is not None:
            login(request, user)
            return redirect(request.GET.get('next') or reverse('accueil:home'))
        else:
            ctx["error"] = "Identifiants incorrects."
            messages.error(request, ctx["error"])  # optional UI feedback

    return render(request, 'connexion/login.html', ctx)


@require_http_methods(["GET"])
def signup_view(request):
    # Design-only placeholder (no business logic yet)
    return render(request, 'connexion/signup.html')

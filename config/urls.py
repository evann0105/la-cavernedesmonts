"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from two_factor.urls import urlpatterns as two_factor_urls
from connexion.views import SecureLoginView
from connexion.admin_site import BoutiqueAdminSite

admin.site.__class__ = BoutiqueAdminSite
# Both public and package login URLs use the same complete 2FA flow.
secure_urls = ([path('account/login/', SecureLoginView.as_view(), name='login')] +
               [entry for entry in two_factor_urls[0] if entry.name != 'login'], 'two_factor')

urlpatterns = [
    path('', include(secure_urls)),
    path('paiement/', include('paiement.urls')),
    path('admin/', admin.site.urls),
    path('', include('accueil.urls')),  # Accueil homepage
    path('', include('core.urls')),     # Core routes
    path('page-produit/', include('pages_products.urls')),  # Dedicated product pages
    path('accounts/', include('connexion.urls')),  # Connexion app
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

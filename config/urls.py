from courriels.views import inbox as test_email_inbox, preview as test_email_preview
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

from connexion.views import SecureLoginView
from connexion.email_views import verify_email
from connexion.admin_site import BoutiqueAdminSite
from core import seo_views

admin.site.__class__ = BoutiqueAdminSite
# Retain historical links without exposing the former OTP setup wizard.
secure_urls = ([
    path('account/login/', SecureLoginView.as_view(), name='login'),
    path('account/two_factor/', verify_email, name='profile'),
    path('account/two_factor/setup/', verify_email, name='setup'),
], 'two_factor')

urlpatterns = [
    path('robots.txt', seo_views.robots, name='seo_robots'),
    path('sitemap.xml', seo_views.sitemap, name='seo_sitemap'),
    path('gestion/emails-test/<uuid:message_id>/', test_email_preview, name='test_email_preview'),
    path('gestion/emails-test/', test_email_inbox, name='test_email_inbox'),
    path('gestion/tableau-de-bord/', include('tableau.urls')),
    path('gestion/stocks/', include('stocks.urls')),
    path('gestion/avoirs-clients/', include('avoirs.urls')),
    path('', include('boutique.urls')),
    path('', include(secure_urls)),
    path('paiement/', include('paiement.urls')),
    path('', include('facturation.urls')),
    path('admin/', admin.site.urls),
    path('', include('retours.urls')),
    path('', include('espace.urls')),
    path('', include('accueil.urls')),  # Accueil homepage
    path('', include('core.urls')),     # Core routes
    path('page-produit/', include('pages_products.urls')),  # Dedicated product pages
    path('accounts/', include('connexion.urls')),  # Connexion app
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

from django.urls import path
from . import views
from .testing_views import guide
app_name = 'paiement'
urlpatterns = [
    path('essais/', guide, name='test_guide'),
    path('', views.checkout, name='checkout'),
    path('confirmation/<uuid:order_id>/', views.confirmation, name='confirmation'),
    path('webhook/', views.webhook, name='webhook'),
]

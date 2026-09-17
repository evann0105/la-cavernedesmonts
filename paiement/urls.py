from django.urls import path
from . import views
app_name = 'paiement'
urlpatterns = [
    path('', views.checkout, name='checkout'),
    path('confirmation/<uuid:order_id>/', views.confirmation, name='confirmation'),
    path('webhook/', views.webhook, name='webhook'),
]

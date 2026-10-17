from django.urls import path
from . import views

app_name = 'facturation'
urlpatterns = [
    path('gestion/commandes/', views.management, name='management'),
    path('commandes/<uuid:pk>/bon-de-commande/', views.document, {'kind': 'purchase_order'}, name='purchase_order'),
    path('commandes/<uuid:pk>/facture/', views.document, {'kind': 'invoice'}, name='invoice'),
    path('commandes/<uuid:pk>/bon-de-livraison/', views.document, {'kind': 'delivery_slip'}, name='delivery_slip'),
    path('gestion/commandes/<uuid:pk>/emettre-facture/', views.issue_invoice, name='issue'),
]

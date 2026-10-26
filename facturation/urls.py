from django.urls import path
from . import views, accounting_views

app_name = 'facturation'
urlpatterns = [
    path('gestion/comptabilite/', accounting_views.overview, name='accounting'),
    path('gestion/comptabilite/ventes.csv', accounting_views.export_sales, name='sales_export'),
    path('gestion/comptabilite/factures.zip', accounting_views.export_invoices, name='invoice_archive'),
    path('gestion/comptabilite/dossier.zip', accounting_views.export_bundle, name='accounting_bundle'),
    path('gestion/commandes/', views.management, name='management'),
    path('commandes/<uuid:pk>/bon-de-commande/', views.document, {'kind': 'purchase_order'}, name='purchase_order'),
    path('commandes/<uuid:pk>/facture/', views.document, {'kind': 'invoice'}, name='invoice'),
    path('commandes/<uuid:pk>/bon-de-livraison/', views.document, {'kind': 'delivery_slip'}, name='delivery_slip'),
    path('gestion/commandes/<uuid:pk>/emettre-facture/', views.issue_invoice, name='issue'),
]

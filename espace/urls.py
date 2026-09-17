from django.urls import path
from . import views
app_name='espace'
urlpatterns=[
 path('promotions/',views.collection,{'kind':'promotions'},name='promotions'),
 path('nouveaux-produits/',views.collection,{'kind':'new'},name='new'),
 path('meilleures-ventes/',views.collection,{'kind':'best'},name='best'),
 path('nous-contacter/',views.info,{'page':'contact'},name='contact'),
 path('content/4-a-propos/',views.info,{'page':'about'},name='about'),
 path('content/3-conditions-utilisation/',views.info,{'page':'terms'},name='terms'),
 path('plan-site/',views.info,{'page':'sitemap'},name='sitemap'),
 path('mon-compte/',views.account,name='account'),
 path('historique-commandes/',views.orders,name='orders'),
 path('historique-commandes/<uuid:pk>/',views.order_detail,name='order'),
 path('avoirs/',views.credits,name='credits'),
 path('adresses/',views.addresses,name='addresses'),
 path('adresses/ajouter/',views.address_edit,name='address_add'),
 path('adresses/<int:pk>/modifier/',views.address_edit,name='address_edit'),
 path('adresses/<int:pk>/supprimer/',views.address_delete,name='address_delete'),
 path('identite/',views.personal,name='personal'),
 path('reduction/',views.vouchers,name='vouchers'),
]

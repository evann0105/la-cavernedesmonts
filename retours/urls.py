from django.urls import path
from . import views
app_name='retours'
urlpatterns=[
 path('retours/dossier/<uuid:pk>/expedition/',views.shipment,name='shipment'),
 path('retours/expedition/<uuid:pk>/justificatif/',views.proof,name='proof'),
 path('retours/',views.entry,name='entry'),
 path('mes-retours/',views.mine,name='mine'),
 path('retours/commande/<uuid:pk>/',views.order_returns,name='order'),
 path('gestion/retours/',views.management,name='manage'),
 path('gestion/retours/<uuid:pk>/',views.manage_detail,name='manage_detail'),
]

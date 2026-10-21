from django.urls import path
from . import views
from . import configuration_views

app_name = 'boutique'
urlpatterns = [
    path('gestion/boutique/reglages/', configuration_views.settings, name='settings'),
    path('gestion/boutique/reglages/coordonnees/', configuration_views.contacts, name='settings_contacts'),
    path('gestion/boutique/reglages/magasin/', configuration_views.store, name='settings_store'),
    path('gestion/boutique/reglages/retours/', configuration_views.returns, name='settings_returns'),
    path('gestion/boutique/reglages/entreprise/', configuration_views.company, name='settings_company'),
    path('gestion/boutique/horaires/', views.hours, name='hours'),
    path('gestion/boutique/horaires/periodes/ajouter/', views.period_edit, name='period_add'),
    path('gestion/boutique/horaires/periodes/<int:pk>/', views.period_edit, name='period_edit'),
    path('gestion/boutique/horaires/periodes/<int:pk>/annuler/', views.period_cancel, name='period_cancel'),
    path('gestion/boutique/horaires/periodes/<int:pk>/reactiver/', views.period_restore, name='period_restore'),
]

from django.urls import path
from . import views

app_name = 'boutique'
urlpatterns = [
    path('gestion/boutique/horaires/', views.hours, name='hours'),
    path('gestion/boutique/horaires/periodes/ajouter/', views.period_edit, name='period_add'),
    path('gestion/boutique/horaires/periodes/<int:pk>/', views.period_edit, name='period_edit'),
    path('gestion/boutique/horaires/periodes/<int:pk>/annuler/', views.period_cancel, name='period_cancel'),
    path('gestion/boutique/horaires/periodes/<int:pk>/reactiver/', views.period_restore, name='period_restore'),
]

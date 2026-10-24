from django.urls import path
from . import management_views as views

app_name = 'homepage'
urlpatterns = [
    path('gestion/accueil/diaporama/', views.slides, name='slides'),
    path('gestion/accueil/grand-air/diaporama/', views.slides, {'section': 'feature'}, name='feature_slides'),
    path('gestion/accueil/sections/', views.sections, name='sections'),
    path('gestion/accueil/sections/<slug:section>/', views.section, name='section'),
    path('gestion/accueil/produits/', views.featured, name='featured'),
]

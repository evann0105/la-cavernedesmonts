from django.urls import path
from . import management_views as views

app_name = 'homepage'
urlpatterns = [
    path('gestion/accueil/sections/', views.sections, name='sections'),
    path('gestion/accueil/sections/<slug:section>/', views.section, name='section'),
    path('gestion/accueil/produits/', views.featured, name='featured'),
]

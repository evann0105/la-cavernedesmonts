from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('produits/', views.product_list, name='product_list'),
    path('produits/<slug:slug>/', views.product_detail, name='product_detail'),
    path('preview/<slug:slug>/', views.product_preview, name='product_preview'),
    path('localisation/', views.location, name='location'),
]

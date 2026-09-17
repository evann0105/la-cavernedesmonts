from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('produits/', views.product_list, name='product_list'),
    path('produits/<slug:slug>/', views.product_detail, name='product_detail'),
    path('preview/<slug:slug>/', views.product_preview, name='product_preview'),
    path('localisation/', views.location, name='location'),
    path('panier/ajouter/<slug:slug>/', views.cart_add, name='cart_add'),
    path('panier/modifier/', views.cart_update, name='cart_update'),
    path('panier/', views.cart, name='cart'),
]

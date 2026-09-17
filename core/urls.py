from django.urls import path
from . import views, catalog_views

app_name = 'core'

urlpatterns = [
    path('gestion/produits/', catalog_views.catalog_list, name='catalog_list'),
    path('gestion/produits/ajouter/', catalog_views.catalog_edit, name='catalog_add'),
    path('gestion/produits/<int:pk>/modifier/', catalog_views.catalog_edit, name='catalog_edit'),
    path('gestion/produits/<int:pk>/apercu/', catalog_views.catalog_preview, name='catalog_preview'),
    path('produits/', views.product_list, name='product_list'),
    path('produits/<slug:slug>/', views.product_detail, name='product_detail'),
    path('preview/<slug:slug>/', views.product_preview, name='product_preview'),
    path('localisation/', views.location, name='location'),
    path('panier/ajouter/<slug:slug>/', views.cart_add, name='cart_add'),
    path('panier/modifier/', views.cart_update, name='cart_update'),
    path('panier/', views.cart, name='cart'),
]

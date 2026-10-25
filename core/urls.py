from django.urls import path
from . import views, catalog_views, promotion_views, sales_views, seo_admin, category_views, catalog_bulk, catalog_duplication
from .catalog_selection import catalog_selection_results
from . import catalog_history_views

app_name = 'core'

urlpatterns = [
    path('gestion/referencement/', seo_admin.overview, name='seo_overview'),
    path('gestion/soldes/', sales_views.sales_dashboard, name='sales_dashboard'),
    path('gestion/soldes/ajouter/', sales_views.sales_create, name='sales_create'),
    path('gestion/soldes/<int:pk>/', sales_views.sales_detail, name='sales_detail'),
    path('gestion/soldes/<int:pk>/annuler/', sales_views.sales_cancel, name='sales_cancel'),
    path('gestion/accueil/', catalog_views.catalog_homepage, name='catalog_homepage'),
    path('gestion/accueil/collections/', catalog_views.catalog_homepage_collections, name='homepage_collections'),
    path('gestion/rubriques/', category_views.category_list, name='category_list'),
    path('gestion/rubriques/ajouter/', category_views.category_create, name='category_create'),
    path('gestion/rubriques/deplacer/', category_views.category_move, name='category_move'),
    path('gestion/rubriques/<int:pk>/modifier/', category_views.category_edit, name='category_edit'),
    path('gestion/rubriques/<int:pk>/menu/', category_views.category_visibility, name='category_visibility'),
    path('gestion/rubriques/<int:pk>/archiver/', category_views.category_archive, name='category_archive'),
    path('gestion/rubriques/<int:pk>/restaurer/', category_views.category_restore, name='category_restore'),
    path('gestion/produits/historique/', catalog_history_views.catalog_history, name='catalog_history'),
    path('gestion/produits/historique/journal/', catalog_history_views.catalog_activity, name='catalog_activity'),
    path('gestion/produits/historique/<int:pk>/', catalog_history_views.catalog_history_detail, name='catalog_history_detail'),
    path('gestion/produits/historique/<int:pk>/annuler/', catalog_history_views.catalog_history_undo, name='catalog_history_undo'),
    path('gestion/produits/modifier-ensemble/', catalog_bulk.catalog_bulk, name='catalog_bulk'),
    path('gestion/produits/selection-resultats/', catalog_selection_results, name='catalog_selection_results'),
    path('gestion/produits/<int:pk>/dupliquer/', catalog_duplication.catalog_duplicate, name='catalog_duplicate'),
    path('gestion/produits/', catalog_views.catalog_list, name='catalog_list'),
    path('gestion/produits/ajouter/', catalog_views.catalog_edit, name='catalog_add'),
    path('gestion/produits/<int:pk>/modifier/', catalog_views.catalog_edit, name='catalog_edit'),
    path('gestion/produits/<int:pk>/reductions/', promotion_views.catalog_promotions, name='catalog_promotions'),
    path('gestion/produits/<int:pk>/reductions/<int:promotion_pk>/annuler/', promotion_views.catalog_promotion_cancel, name='catalog_promotion_cancel'),
    path('gestion/produits/<int:pk>/apercu/', catalog_views.catalog_preview, name='catalog_preview'),
    path('produits/', views.product_list, name='product_list'),
    path('produits/<slug:slug>/', views.product_detail, name='product_detail'),
    path('produits/<slug:slug>/prix/', views.product_prices, name='product_prices'),
    path('preview/<slug:slug>/', views.product_preview, name='product_preview'),
    path('magasins/', views.location, name='location'),
    path('localisation/', views.location_legacy, name='location_legacy'),
    path('panier/ajouter/<slug:slug>/', views.cart_add, name='cart_add'),
    path('panier/modifier/', views.cart_update, name='cart_update'),
    path('panier/', views.cart, name='cart'),
]

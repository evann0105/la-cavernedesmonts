from django.urls import path
from . import views, import_views
app_name = 'stocks'
urlpatterns = [
    path('importer/', import_views.inventory, name='import'),
    path('exporter/', import_views.download, name='export'),
    path('', views.overview, name='overview'),
    path('<int:pk>/', views.product_stock, name='product'),
    path('<int:pk>/<int:variant_pk>/ajuster/', views.adjustment, name='adjust'),
    path('<int:pk>/<int:variant_pk>/', views.product_stock, name='edit'),
]

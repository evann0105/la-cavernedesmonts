from django.urls import path

from . import views

app_name = 'historique'
urlpatterns = [
    path('', views.index, name='index'),
    path('produits/<int:pk>/', views.product, name='product'),
    path('accueil/', views.homepage, name='homepage'),
    path('versions/<int:pk>/', views.detail, name='detail'),
    path('versions/<int:pk>/apercu/', views.preview, name='preview'),
    path('versions/<int:pk>/apercu/contenu/', views.frame, name='frame'),
    path('versions/<int:pk>/restaurer/', views.restore, name='restore'),
]

from django.urls import path
from . import views

app_name = 'phototheque'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('photos.json', views.photos_json, name='photos_json'),
    path('ajouter/', views.upload, name='upload'),
    path('ranger/', views.move, name='move'),
    path('dossiers/', views.folders, name='folders'),
    path('dossiers/<int:pk>/', views.folder_edit, name='folder_edit'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/<slug:action>/', views.photo_action, name='photo_action'),
]

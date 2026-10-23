from django.urls import path
from . import views

app_name = 'expeditions'
urlpatterns = [
    path('gestion/expeditions/', views.overview, name='overview'),
    path('gestion/expeditions/<uuid:pk>/', views.detail, name='detail'),
]

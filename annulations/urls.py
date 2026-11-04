from django.urls import path
from . import views
from .worklist import overview
from .live import status

app_name = 'annulations'
urlpatterns = [
    path('gestion/annulations/', overview, name='overview'),
    path('gestion/expeditions/<uuid:pk>/annuler/', views.detail, name='detail'),
    path('gestion/expeditions/<uuid:pk>/annuler/suivi/', status, name='status'),
]

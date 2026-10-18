from django.urls import path
from . import views

app_name = 'boutique'
urlpatterns = [path('gestion/boutique/horaires/', views.hours, name='hours')]

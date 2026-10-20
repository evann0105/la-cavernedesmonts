from django.urls import path
from . import views

app_name = 'avoirs'
urlpatterns = [
    path('', views.management, name='management'),
    path('nouveau/', views.create, name='create'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/modifier/', views.edit, name='edit'),
    path('<int:pk>/activer/', views.toggle, name='toggle'),
    path('<int:pk>/envoyer/', views.notify, name='notify'),
]

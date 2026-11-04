from django.urls import path
from . import dashboard, views

app_name = 'suspensions'
urlpatterns = [
    path('', views.overview, name='overview'),
    path('nouvelle/', views.create, name='create'),
    path('statut-tableau/', dashboard.status, name='dashboard_status'),
    path('<int:pk>/', views.edit, name='edit'),
    path('<int:pk>/dupliquer/', views.duplicate, name='duplicate'),
    path('<int:pk>/<slug:action>/', views.action, name='action'),
]

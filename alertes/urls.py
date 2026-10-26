from django.urls import path
from . import views

app_name = 'alertes'
urlpatterns = [
    path('', views.emails, name='emails'),
    path('messages/<str:source>/<str:pk>/', views.email_detail, name='email_detail'),
    path('reglages/', views.settings_view, name='settings'),
    path('stocks/', views.stocks, name='stocks'),
    path('stocks/<int:variant_id>/', views.stock_rule, name='stock_rule'),
]

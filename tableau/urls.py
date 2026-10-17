from django.urls import path
from .views import dashboard
app_name='tableau'
urlpatterns=[path('', dashboard, name='dashboard')]

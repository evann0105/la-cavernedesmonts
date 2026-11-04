from django.urls import path
from .public import status

app_name = 'suspensions_public'
urlpatterns = [path('etat-commandes/', status, name='status')]

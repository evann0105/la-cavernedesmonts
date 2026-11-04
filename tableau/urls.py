from django.urls import path
from .views import dashboard
from .home import home, tools
app_name='tableau'
urlpatterns = [
    path('', home, name='home'),
    path('outils/', tools, name='tools'),
    path('tableau-de-bord/', dashboard, name='dashboard'),
]

from django.urls import path
from . import views

app_name = 'clients'
urlpatterns = [
    path('', views.directory, name='directory'),
    path('sav/', views.case_list, name='case_list'),
    path('sav/nouveau/', views.case_create, name='case_create'),
    path('sav/<uuid:pk>/', views.case_detail, name='case_detail'),
    path('<str:key>/', views.profile, name='profile'),
]

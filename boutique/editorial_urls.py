from django.urls import path
from . import editorial_views as views

app_name = 'editorial'
urlpatterns = [
    path('gestion/pages-annonces/', views.overview, name='overview'),
    path('gestion/pages-annonces/pages/<slug:key>/', views.page_edit, name='page_edit'),
    path('gestion/pages-annonces/faq/', views.faq_list, name='faq_list'),
    path('gestion/pages-annonces/faq/nouvelle/', views.faq_edit, name='faq_create'),
    path('gestion/pages-annonces/faq/<int:pk>/', views.faq_edit, name='faq_edit'),
    path('gestion/pages-annonces/annonces/', views.announcements, name='announcements'),
    path('gestion/pages-annonces/annonces/nouvelle/', views.announcement_edit, name='announcement_create'),
    path('gestion/pages-annonces/annonces/<int:pk>/', views.announcement_edit, name='announcement_edit'),
    path('questions-frequentes/', views.faq, name='faq'),
]

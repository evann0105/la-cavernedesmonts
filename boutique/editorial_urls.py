from django.urls import path
from . import editorial_views as views

app_name = 'editorial'
urlpatterns = [
    path('gestion/pages-annonces/', views.overview, name='overview'),
    path('gestion/pages-annonces/pages/<slug:key>/', views.page_edit, name='page_edit'),
    path('gestion/pages-annonces/pages/<slug:key>/apercu/', views.page_preview, name='page_preview'),
    path('gestion/pages-annonces/pages/<slug:key>/apercu/contenu/', views.page_preview_frame, name='page_preview_frame'),
    path('gestion/pages-annonces/pages/<slug:key>/historique/', views.page_history, name='page_history'),
    path('gestion/pages-annonces/pages/<slug:key>/versions/<int:pk>/', views.page_version_preview, name='page_version_preview'),
    path('gestion/pages-annonces/pages/<slug:key>/versions/<int:pk>/contenu/', views.page_version_frame, name='page_version_frame'),
    path('gestion/pages-annonces/pages/<slug:key>/versions/<int:pk>/restaurer/', views.page_restore, name='page_restore'),
    path('gestion/pages-annonces/pages/<slug:key>/publier/', views.page_publish, name='page_publish'),
    path('gestion/pages-annonces/faq/', views.faq_list, name='faq_list'),
    path('gestion/pages-annonces/faq/nouvelle/', views.faq_edit, name='faq_create'),
    path('gestion/pages-annonces/faq/<int:pk>/', views.faq_edit, name='faq_edit'),
    path('gestion/pages-annonces/annonces/', views.announcements, name='announcements'),
    path('gestion/pages-annonces/annonces/chevauchements/', views.announcement_overlap_check, name='announcement_overlap_check'),
    path('gestion/pages-annonces/annonces/nouvelle/', views.announcement_edit, name='announcement_create'),
    path('gestion/pages-annonces/annonces/<int:pk>/', views.announcement_edit, name='announcement_edit'),
    path('questions-frequentes/', views.faq, name='faq'),
]

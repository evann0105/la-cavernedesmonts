from django.urls import path
from . import views, correction_views, attachment_views

app_name = 'clients'
urlpatterns = [
    path('', views.directory, name='directory'),
    path('sav/', views.case_list, name='case_list'),
    path('sav/nouveau/', views.case_create, name='case_create'),
    path('sav/<uuid:pk>/modifier/', correction_views.case_edit, name='case_edit'),
    path('sav/<uuid:pk>/pieces-jointes/ajouter/', attachment_views.attachment_add, name='attachment_add'),
    path('sav/<uuid:pk>/pieces-jointes/<uuid:attachment_id>/', attachment_views.attachment_download, name='attachment_download'),
    path('sav/<uuid:pk>/pieces-jointes/<uuid:attachment_id>/retirer/', attachment_views.attachment_remove, name='attachment_remove'),
    path('sav/<uuid:pk>/', views.case_detail, name='case_detail'),
    path('<str:key>/', views.profile, name='profile'),
]

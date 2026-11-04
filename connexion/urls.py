from django.urls import path
from . import views, email_views, password_reset_views

app_name = 'connexion'

urlpatterns = [
    path('mot-de-passe-oublie/', password_reset_views.request_reset, name='password_reset'),
    path('mot-de-passe-oublie/demande/', password_reset_views.reset_done, name='password_reset_done'),
    path('nouveau-mot-de-passe/<str:uidb64>/<str:token>/', password_reset_views.confirm_reset, name='password_reset_confirm'),
    path('mot-de-passe-modifie/', password_reset_views.reset_complete, name='password_reset_complete'),
    path('verification-email/', email_views.verify_email, name='verify_email'),
    path('confirmer-email/<str:token>/', email_views.confirm_email, name='confirm_email'),
    path("logout/", views.logout_view, name="logout"),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
]

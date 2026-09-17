from django.urls import path
from . import views, email_views

app_name = 'connexion'

urlpatterns = [
    path('verification-email/', email_views.verify_email, name='verify_email'),
    path('confirmer-email/<str:token>/', email_views.confirm_email, name='confirm_email'),
    path("logout/", views.logout_view, name="logout"),
    path('login/', views.login_view, name='login'),
    path('signup/', views.signup_view, name='signup'),
]
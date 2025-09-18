from django.urls import path
from . import views

app_name = 'pages_products'

urlpatterns = [
    path('<slug:slug>/', views.product_page, name='product_page'),
]
from django.contrib import admin
from django.urls import path

from payment_gateway import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health', views.health, name='health'),
    path('operations', views.create_operation),
    path('operations/<str:operation_id>/submit', views.submit_operation),
]

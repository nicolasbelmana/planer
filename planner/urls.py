from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="planner/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("ordenes/nueva/", views.work_order_create, name="work-order-create"),
    path("ordenes/<int:pk>/", views.work_order_detail, name="work-order-detail"),
    path("ordenes/<int:pk>/estado/", views.work_order_status, name="work-order-status"),
    path(
        "ordenes/<int:pk>/tecnicos/",
        views.work_order_technicians,
        name="work-order-technicians",
    ),
    path(
        "ordenes/<int:pk>/solicitar-cambio/",
        views.work_order_change_request,
        name="work-order-change-request",
    ),
    path("solicitudes/", views.change_request_list, name="change-request-list"),
    path(
        "solicitudes/<int:pk>/resolver/",
        views.change_request_resolve,
        name="change-request-resolve",
    ),
]

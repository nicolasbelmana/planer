from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="planner/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("equipos/", views.equipment_list, name="equipment-list"),
    path("activos/", views.asset_list, name="asset-list"),
    path("activos/nuevo/", views.asset_create, name="asset-create"),
    path("activos/<int:pk>/editar/", views.asset_edit, name="asset-edit"),
    path("activos/<int:pk>/", views.asset_detail, name="asset-detail"),
    path(
        "activos/conexiones/nueva/",
        views.asset_connection_create,
        name="asset-connection-create",
    ),
    path(
        "preventivos/",
        views.preventive_plan_list,
        name="preventive-plan-list",
    ),
    path(
        "preventivos/nuevo/",
        views.preventive_plan_create,
        name="preventive-plan-create",
    ),
    path(
        "preventivos/<int:pk>/registrar/",
        views.preventive_maintenance_record,
        name="preventive-maintenance-record",
    ),
    path("personal/", views.staff_list, name="staff-list"),
    path("personal/nuevo/", views.staff_create, name="staff-create"),
    path("materiales/", views.materials_dashboard, name="materials-dashboard"),
    path(
        "materiales/pedidos/<int:pk>/entregar/",
        views.material_request_deliver,
        name="material-request-deliver",
    ),
    path("ordenes/nueva/", views.work_order_create, name="work-order-create"),
    path("ordenes/<int:pk>/", views.work_order_detail, name="work-order-detail"),
    path("ordenes/<int:pk>/pdf/", views.work_order_pdf, name="work-order-pdf"),
    path("ordenes/<int:pk>/estado/", views.work_order_status, name="work-order-status"),
    path(
        "ordenes/<int:pk>/materiales/recibidos/",
        views.work_order_materials_received,
        name="work-order-materials-received",
    ),
    path(
        "ordenes/<int:pk>/diario/nuevo/",
        views.work_order_diary_entry_create,
        name="work-order-diary-create",
    ),
    path(
        "ordenes/<int:pk>/materiales/pedir/",
        views.work_order_material_request_create,
        name="work-order-material-request-create",
    ),
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

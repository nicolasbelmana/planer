from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    ChangeRequestForm,
    ChangeRequestResolutionForm,
    StatusUpdateForm,
    TechnicianAssignmentForm,
    WorkOrderCreateForm,
)
from .models import Equipment, MachinePart, WorkOrder, WorkOrderChangeRequest
from .roles import CHIEF, SUPERVISOR, has_role
from .services import (
    assign_technicians,
    create_work_order,
    resolve_change_request,
    submit_change_request,
    update_work_order_status,
)


def _can_view_order(user, order):
    return (
        has_role(user, CHIEF)
        or order.supervisors.filter(pk=user.pk).exists()
        or order.technicians.filter(pk=user.pk).exists()
    )


def _require_order_access(user, order):
    if not _can_view_order(user, order):
        raise PermissionDenied("No tienes acceso a esta orden.")


@login_required
def dashboard(request):
    orders = WorkOrder.objects.select_related("equipment").prefetch_related(
        "supervisors", "technicians"
    )
    if not has_role(request.user, CHIEF):
        orders = orders.filter(
            Q(supervisors=request.user) | Q(technicians=request.user)
        ).distinct()
    status = request.GET.get("status", "")
    if status in WorkOrder.Status.values:
        orders = orders.filter(status=status)
    order_list = list(orders)
    overdue = request.GET.get("overdue", "")
    if overdue == "yes":
        today = timezone.localdate()
        order_list = [
            order
            for order in order_list
            if order.is_overdue and order.due_date < today
        ]
    elif overdue == "no":
        order_list = [order for order in order_list if not order.is_overdue]
    return render(
        request,
        "planner/dashboard.html",
        {
            "orders": order_list,
            "statuses": WorkOrder.Status.choices,
            "selected_status": status,
            "selected_overdue": overdue,
            "can_create": has_role(request.user, CHIEF),
            "pending_count": (
                WorkOrderChangeRequest.objects.filter(
                    status=WorkOrderChangeRequest.Status.PENDING
                ).count()
                if has_role(request.user, CHIEF)
                else 0
            ),
        },
    )


@login_required
def work_order_create(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear órdenes.")
    form = WorkOrderCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            order = create_work_order(
                actor=request.user,
                values={
                    key: form.cleaned_data[key]
                    for key in form.fields
                },
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "La orden fue creada.")
            return redirect("work-order-detail", pk=order.pk)
    return render(request, "planner/work_order_form.html", {"form": form})


@login_required
def work_order_detail(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.select_related("equipment", "machine_part", "created_by")
        .prefetch_related("supervisors", "technicians", "history__actor", "change_requests"),
        pk=pk,
    )
    _require_order_access(request.user, order)
    is_assigned_supervisor = (
        has_role(request.user, SUPERVISOR)
        and order.supervisors.filter(pk=request.user.pk).exists()
    )
    return render(
        request,
        "planner/work_order_detail.html",
        {
            "order": order,
            "can_manage": is_assigned_supervisor,
            "can_review": has_role(request.user, CHIEF),
            "status_form": StatusUpdateForm(order),
            "technician_form": TechnicianAssignmentForm(
                initial={"technicians": order.technicians.values_list("pk", flat=True)}
            ),
        },
    )


@login_required
@require_POST
def work_order_status(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.prefetch_related("supervisors"),
        pk=pk,
    )
    _require_order_access(request.user, order)
    form = StatusUpdateForm(order, request.POST)
    if form.is_valid():
        try:
            update_work_order_status(
                order=order,
                actor=request.user,
                new_status=form.cleaned_data["status"],
            )
        except (PermissionDenied, ValidationError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "El estado de la orden fue actualizado.")
    else:
        messages.error(request, "Selecciona un estado válido.")
    return redirect("work-order-detail", pk=pk)


@login_required
@require_POST
def work_order_technicians(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.prefetch_related("supervisors", "technicians"),
        pk=pk,
    )
    _require_order_access(request.user, order)
    form = TechnicianAssignmentForm(request.POST)
    if form.is_valid():
        try:
            assign_technicians(
                order=order,
                actor=request.user,
                technicians=form.cleaned_data["technicians"],
            )
        except (PermissionDenied, ValidationError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "La asignación de técnicos fue actualizada.")
    else:
        messages.error(request, "Revisa la selección de técnicos.")
    return redirect("work-order-detail", pk=pk)


@login_required
def work_order_change_request(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.prefetch_related("supervisors", "change_requests"),
        pk=pk,
    )
    _require_order_access(request.user, order)
    if not has_role(request.user, SUPERVISOR) or not order.supervisors.filter(
        pk=request.user.pk
    ).exists():
        raise PermissionDenied("Solo un supervisor asignado puede solicitar cambios.")
    form = ChangeRequestForm(order, request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            change_request = submit_change_request(
                order=order,
                actor=request.user,
                proposed_values=form.proposed_values(),
                reason=form.cleaned_data["reason"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "La solicitud fue enviada al jefe para su aprobación.")
            return redirect("work-order-detail", pk=change_request.work_order_id)
    return render(
        request,
        "planner/change_request_form.html",
        {"form": form, "order": order},
    )


@login_required
def change_request_list(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede revisar solicitudes.")
    requests = list(
        WorkOrderChangeRequest.objects.select_related("work_order", "requested_by").filter(
            status=WorkOrderChangeRequest.Status.PENDING
        )
    )
    equipment_ids = {
        change.proposed_values["equipment"] for change in requests
    }
    part_ids = {
        change.proposed_values["machine_part"]
        for change in requests
        if change.proposed_values["machine_part"] is not None
    }
    supervisor_ids = {
        supervisor_id
        for change in requests
        for supervisor_id in change.proposed_values["supervisors"]
    }
    equipment_names = dict(
        Equipment.objects.filter(pk__in=equipment_ids).values_list("pk", "name")
    )
    parts = {
        part.pk: str(part)
        for part in MachinePart.objects.filter(pk__in=part_ids).select_related("equipment")
    }
    supervisors = dict(
        get_user_model()
        .objects.filter(pk__in=supervisor_ids)
        .values_list("pk", "username")
    )
    for change in requests:
        proposed = change.proposed_values
        change.proposed_display = {
            "start_date": date.fromisoformat(proposed["start_date"]),
            "estimated_days": proposed["estimated_days"],
            "equipment": equipment_names.get(proposed["equipment"], "No disponible"),
            "machine_part": parts.get(proposed["machine_part"], "Equipo completo")
            if proposed["machine_part"] is not None
            else "Equipo completo",
            "supervisors": [
                supervisors.get(user_id, "Usuario no disponible")
                for user_id in proposed["supervisors"]
            ],
        }
    return render(request, "planner/change_request_list.html", {"requests": requests})


@login_required
@require_POST
def change_request_resolve(request, pk):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede resolver solicitudes.")
    change_request = get_object_or_404(
        WorkOrderChangeRequest.objects.select_related("work_order"),
        pk=pk,
    )
    form = ChangeRequestResolutionForm(request.POST)
    if form.is_valid():
        try:
            resolve_change_request(
                request=change_request,
                actor=request.user,
                approve=form.cleaned_data["decision"] == "approve",
                comment=form.cleaned_data["comment"],
            )
        except ValidationError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "La solicitud fue resuelta.")
    else:
        messages.error(request, "Selecciona una decisión válida.")
    return redirect("change-request-list")

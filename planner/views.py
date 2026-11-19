from io import BytesIO
from datetime import date
from datetime import timedelta
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .forms import (
    AssetConnectionForm,
    AssetForm,
    ChangeRequestForm,
    ChangeRequestResolutionForm,
    EquipmentForm,
    MachinePartForm,
    PreventiveMaintenancePlanForm,
    PreventiveMaintenanceRecordForm,
    StaffUserCreationForm,
    StatusUpdateForm,
    TechnicianAssignmentForm,
    WorkOrderCreateForm,
)
from .models import (
    Asset,
    AssetConnection,
    AssetType,
    Equipment,
    MachinePart,
    PreventiveMaintenancePlan,
    PreventiveMaintenanceRecord,
    WorkOrder,
    WorkOrderChangeRequest,
)
from .roles import (
    CHIEF,
    OPERATOR,
    ROLE_NAMES,
    SUPERVISOR,
    TECHNICIAN,
    has_role,
)
from .services import (
    assign_technicians,
    can_manage_work_order,
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


def _require_assigned_supervisor(user, order):
    if not has_role(user, SUPERVISOR) or not order.supervisors.filter(pk=user.pk).exists():
        raise PermissionDenied("Solo un supervisor asignado puede modificar esta orden.")


def _require_order_manager(user, order):
    if not can_manage_work_order(user, order):
        raise PermissionDenied(
            "Solo el jefe o un supervisor asignado puede actualizar esta orden."
        )


@login_required
def dashboard(request):
    orders = WorkOrder.objects.select_related("equipment").prefetch_related(
        "supervisors", "technicians", "assets"
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
def equipment_list(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede administrar equipos.")
    equipment_form = EquipmentForm(prefix="equipment")
    part_form = MachinePartForm(prefix="part")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create_equipment":
            equipment_form = EquipmentForm(request.POST, prefix="equipment")
            if equipment_form.is_valid():
                equipment = equipment_form.save()
                messages.success(request, f"Se creó el equipo {equipment.name}.")
                return redirect("equipment-list")
        elif action == "create_part":
            part_form = MachinePartForm(request.POST, prefix="part")
            if part_form.is_valid():
                part = part_form.save()
                messages.success(request, f"Se agregó {part.name} a {part.equipment.name}.")
                return redirect("equipment-list")
        else:
            raise PermissionDenied("Acción de administración no válida.")
    equipment = Equipment.objects.prefetch_related("parts").all()
    return render(
        request,
        "planner/equipment_list.html",
        {
            "equipment": equipment,
            "equipment_form": equipment_form,
            "part_form": part_form,
        },
    )


@login_required
def asset_list(request):
    if not has_role(request.user, CHIEF) and not has_role(request.user, SUPERVISOR):
        raise PermissionDenied("Solo jefes y supervisores pueden consultar los activos.")
    assets = list(
        Asset.objects.select_related("asset_type", "location", "parent").order_by(
            "asset_type__name", "name"
        )
    )
    children_by_parent = {}
    roots = []
    for asset in assets:
        asset.tree_children = []
        if asset.parent_id:
            children_by_parent.setdefault(asset.parent_id, []).append(asset)
        else:
            roots.append(asset)
    for asset in assets:
        asset.tree_children = children_by_parent.get(asset.pk, [])
    for asset in assets:
        asset.floor_asset = asset.floor
    return render(request, "planner/asset_list.html", {"asset_roots": roots})


@login_required
def asset_create(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear activos.")
    initial = {}
    parent_id = request.GET.get("parent")
    if parent_id:
        parent = get_object_or_404(Asset, pk=parent_id)
        initial["parent"] = parent.pk
        if not parent.asset_type.is_location and parent.floor:
            initial["location"] = parent.floor.pk
    form = AssetForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        asset = form.save()
        messages.success(request, f"Se agregó {asset.name} al árbol de activos.")
        return redirect("asset-detail", pk=asset.pk)
    return render(request, "planner/asset_form.html", {"form": form})


@login_required
def asset_edit(request, pk):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede editar activos.")
    asset = get_object_or_404(Asset, pk=pk)
    form = AssetForm(request.POST or None, instance=asset)
    if request.method == "POST" and form.is_valid():
        updated = form.save()
        messages.success(request, f"Se actualizaron los datos de {updated.name}.")
        return redirect("asset-detail", pk=updated.pk)
    return render(
        request,
        "planner/asset_form.html",
        {"form": form, "asset": asset},
    )


@login_required
def asset_detail(request, pk):
    if not has_role(request.user, CHIEF) and not has_role(request.user, SUPERVISOR):
        raise PermissionDenied("Solo jefes y supervisores pueden consultar los activos.")
    asset = get_object_or_404(
        Asset.objects.select_related("asset_type", "parent", "location")
        .prefetch_related(
            "children__asset_type",
            "outgoing_connections__target__asset_type",
            "incoming_connections__source__asset_type",
            "maintenance_plans__supervisors",
        ),
        pk=pk,
    )
    return render(
        request,
        "planner/asset_detail.html",
        {
            "asset": asset,
            "can_manage": has_role(request.user, CHIEF),
            "technical_specs": [
                {
                    "label": specification["label"],
                    "value": asset.specifications.get(specification["key"], ""),
                    "unit": specification.get("unit", ""),
                }
                for specification in asset.asset_type.specification_fields
            ],
        },
    )


@login_required
def asset_connection_create(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede vincular activos.")
    initial = {}
    source_id = request.GET.get("source")
    if source_id:
        initial["source"] = get_object_or_404(Asset, pk=source_id).pk
    form = AssetConnectionForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        connection = form.save(commit=False)
        try:
            connection.full_clean()
            connection.save()
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Se registró la relación entre activos.")
            return redirect("asset-detail", pk=connection.source_id)
    return render(request, "planner/asset_connection_form.html", {"form": form})


@login_required
def preventive_plan_list(request):
    if not has_role(request.user, CHIEF) and not has_role(request.user, SUPERVISOR):
        raise PermissionDenied("Solo jefes y supervisores pueden consultar los planes preventivos.")
    plans = PreventiveMaintenancePlan.objects.select_related(
        "asset", "asset__asset_type"
    ).prefetch_related("supervisors", "records")
    if not has_role(request.user, CHIEF):
        plans = plans.filter(supervisors=request.user).distinct()
    return render(
        request,
        "planner/preventive_plan_list.html",
        {"plans": plans, "today": timezone.localdate()},
    )


@login_required
def preventive_plan_create(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear planes preventivos.")
    initial = {}
    asset_id = request.GET.get("asset")
    if asset_id:
        initial["asset"] = get_object_or_404(Asset, pk=asset_id).pk
    form = PreventiveMaintenancePlanForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        plan = form.save(commit=False)
        plan.created_by = request.user
        plan.full_clean()
        plan.save()
        form.save_m2m()
        messages.success(request, "Se creó el plan preventivo.")
        return redirect("preventive-plan-list")
    return render(request, "planner/preventive_plan_form.html", {"form": form})


@login_required
@require_POST
def preventive_maintenance_record(request, pk):
    plan = get_object_or_404(
        PreventiveMaintenancePlan.objects.select_related("asset"),
        pk=pk,
    )
    if not has_role(request.user, CHIEF) and not plan.supervisors.filter(
        pk=request.user.pk
    ).exists():
        raise PermissionDenied("Solo el jefe o un supervisor responsable puede registrar el mantenimiento.")
    form = PreventiveMaintenanceRecordForm(request.POST)
    if form.is_valid():
        record = form.save(commit=False)
        record.plan = plan
        record.performed_by = request.user
        if record.performed_on > timezone.localdate():
            form.add_error("performed_on", "La fecha no puede estar en el futuro.")
        else:
            with transaction.atomic():
                locked_plan = PreventiveMaintenancePlan.objects.select_for_update().get(
                    pk=plan.pk
                )
                record.save()
                locked_plan.next_due_date = record.performed_on + timedelta(
                    days=locked_plan.interval_days
                )
                locked_plan.save(update_fields=["next_due_date"])
            messages.success(
                request,
                f"Mantenimiento registrado. Próxima fecha: {locked_plan.next_due_date:%d/%m/%Y}.",
            )
            return redirect("preventive-plan-list")
    messages.error(request, "No se pudo registrar el mantenimiento; revisa los datos.")
    return redirect("preventive-plan-list")


@login_required
def staff_list(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede administrar usuarios.")
    users = (
        get_user_model()
        .objects.filter(Q(groups__name__in=ROLE_NAMES) | Q(is_superuser=True))
        .prefetch_related("groups")
        .order_by("username")
        .distinct()
    )
    return render(request, "planner/staff_list.html", {"staff_users": users})


@login_required
def staff_create(request):
    if not has_role(request.user, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear usuarios.")
    form = StaffUserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(
            request,
            f"Se creó el usuario {user.get_username()} con rol {form.cleaned_data['role']}.",
        )
        return redirect("staff-list")
    return render(request, "planner/staff_form.html", {"form": form})


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
        .prefetch_related(
            "supervisors",
            "technicians",
            "assets__asset_type",
            "history__actor",
            "change_requests",
        ),
        pk=pk,
    )
    _require_order_access(request.user, order)
    return render(
        request,
        "planner/work_order_detail.html",
        _work_order_detail_context(request.user, order),
    )


def _work_order_detail_context(user, order):
    is_assigned_supervisor = (
        has_role(user, SUPERVISOR) and order.supervisors.filter(pk=user.pk).exists()
    )
    return {
        "order": order,
        "can_update_progress": can_manage_work_order(user, order),
        "can_assign_operators": is_assigned_supervisor,
        "can_request_change": is_assigned_supervisor,
        "can_view_assets": has_role(user, CHIEF) or has_role(user, SUPERVISOR),
        "linked_assets": order.assets.select_related("asset_type").all(),
        "status_form": StatusUpdateForm(order),
        "technician_form": TechnicianAssignmentForm(
            initial={"technicians": order.technicians.values_list("pk", flat=True)}
        ),
    }


@login_required
def work_order_pdf(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.select_related("equipment", "machine_part")
        .prefetch_related(
            "supervisors",
            "technicians",
            "assets__asset_type",
        ),
        pk=pk,
    )
    _require_order_access(request.user, order)

    def paragraph(value, style):
        safe = escape(str(value)).replace("\n", "<br/>")
        return Paragraph(safe or "—", style)

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Orden de trabajo OT-{order.pk}",
        author="Maintenance Planner",
    )
    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "TableHeader",
        parent=styles["BodyText"],
        textColor=colors.white,
        fontName="Helvetica-Bold",
    )
    title_style = ParagraphStyle(
        "WorkOrderTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        textColor=colors.HexColor("#243b53"),
        spaceAfter=8 * mm,
    )
    body_style = styles["BodyText"]
    elements = [
        Paragraph(f"Orden de trabajo OT-{order.pk}", title_style),
        Paragraph(escape(order.title), styles["Heading2"]),
        Spacer(1, 4 * mm),
    ]
    rows = [
        [paragraph("Campo", header_style), paragraph("Detalle", header_style)],
        [paragraph("Estado", body_style), paragraph(order.get_status_display(), body_style)],
        [
            paragraph("Vencimiento", body_style),
            paragraph(
                f"{order.due_date.strftime('%d/%m/%Y')} "
                f"({'VENCIDA' if order.is_overdue else 'En plazo'})",
                body_style,
            ),
        ],
        [
            paragraph("Equipo legado", body_style),
            paragraph(order.equipment.name if order.equipment else "—", body_style),
        ],
        [
            paragraph("Activos vinculados", body_style),
            paragraph(
                ", ".join(
                    f"{asset.name} ({asset.asset_type.name})" for asset in order.assets.all()
                ),
                body_style,
            ),
        ],
        [
            paragraph("Parte", body_style),
            paragraph(order.machine_part.name if order.machine_part else "Equipo completo", body_style),
        ],
        [paragraph("Descripción", body_style), paragraph(order.description, body_style)],
        [
            paragraph("Inicio", body_style),
            paragraph(order.start_date.strftime("%d/%m/%Y"), body_style),
        ],
        [
            paragraph("Duración", body_style),
            paragraph(f"{order.estimated_days} días corridos", body_style),
        ],
        [
            paragraph("Supervisores", body_style),
            paragraph(", ".join(user.get_username() for user in order.supervisors.all()), body_style),
        ],
        [
            paragraph("Operarios y técnicos", body_style),
            paragraph(", ".join(user.get_username() for user in order.technicians.all()), body_style),
        ],
    ]
    table = Table(rows, colWidths=[42 * mm, 128 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#243b53")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#edf2f7")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    elements.append(table)
    document.build(elements)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="orden-{order.pk}.pdf"'
    return response


@login_required
@require_POST
def work_order_status(request, pk):
    order = get_object_or_404(
        WorkOrder.objects.prefetch_related("supervisors"),
        pk=pk,
    )
    _require_order_access(request.user, order)
    _require_order_manager(request.user, order)
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
    _require_assigned_supervisor(request.user, order)
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
            messages.success(request, "La asignación de operarios fue actualizada.")
    else:
        messages.error(request, "Revisa la selección de operarios.")
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
    asset_ids = {
        asset_id
        for change in requests
        for asset_id in change.proposed_values.get("assets", [])
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
    assets = {
        asset_id: (name, type_name)
        for asset_id, name, type_name in Asset.objects.filter(pk__in=asset_ids).values_list(
            "pk", "name", "asset_type__name"
        )
    }
    for change in requests:
        proposed = change.proposed_values
        change.proposed_display = {
            "start_date": date.fromisoformat(proposed["start_date"]),
            "estimated_days": proposed["estimated_days"],
            "equipment": equipment_names.get(proposed["equipment"], "No disponible")
            if proposed["equipment"] is not None
            else "—",
            "machine_part": parts.get(proposed["machine_part"], "Equipo completo")
            if proposed["machine_part"] is not None
            else "Equipo completo",
            "supervisors": [
                supervisors.get(user_id, "Usuario no disponible")
                for user_id in proposed["supervisors"]
            ],
            "assets": [
                f"{assets[asset_id][0]} ({assets[asset_id][1]})"
                for asset_id in proposed.get("assets", [])
                if asset_id in assets
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

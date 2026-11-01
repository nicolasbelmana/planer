import logging
from datetime import date
from smtplib import SMTPException

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import (
    Asset,
    Equipment,
    MachinePart,
    Material,
    MaterialStockMovement,
    WorkOrder,
    WorkOrderChangeRequest,
    WorkOrderDiaryEntry,
    WorkOrderHistory,
    WorkOrderMaterialRequest,
)
from .roles import CHIEF, STOREKEEPER, SUPERVISOR, WORK_ORDER_STAFF_ROLES, has_role

logger = logging.getLogger(__name__)

STATUS_TRANSITIONS = {
    WorkOrder.Status.PREPARATION: {
        WorkOrder.Status.WAITING_MATERIALS,
        WorkOrder.Status.IN_PROGRESS,
    },
    WorkOrder.Status.WAITING_MATERIALS: {WorkOrder.Status.IN_PROGRESS},
    WorkOrder.Status.IN_PROGRESS: {WorkOrder.Status.COMPLETED},
    WorkOrder.Status.COMPLETED: set(),
}
def _id_list(queryset):
    return sorted(queryset.values_list("id", flat=True))


def work_order_snapshot(order):
    return {
        "title": order.title,
        "description": order.description,
        "equipment": order.equipment_id,
        "machine_part": order.machine_part_id,
        "assets": _id_list(order.assets),
        "start_date": order.start_date.isoformat(),
        "estimated_days": order.estimated_days,
        "status": order.status,
        "supervisors": _id_list(order.supervisors),
        "technicians": _id_list(order.technicians),
    }


def approval_snapshot(order):
    return {
        "start_date": order.start_date.isoformat(),
        "estimated_days": order.estimated_days,
        "equipment": order.equipment_id,
        "machine_part": order.machine_part_id,
        "supervisors": _id_list(order.supervisors),
        "assets": _id_list(order.assets),
    }


def _record_history(order, actor, action, old_values=None, new_values=None):
    return WorkOrderHistory.objects.create(
        work_order=order,
        actor=actor,
        action=action,
        old_values=old_values or {},
        new_values=new_values or {},
    )


def _send_email(subject, body, recipients):
    recipients = sorted({email for email in recipients if email})
    if not recipients:
        return
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
    except (SMTPException, OSError):
        logger.exception("No se pudo enviar la notificación por email: %s", subject)


def _notify_order(order, subject, body):
    recipients = list(order.supervisors.values_list("email", flat=True))
    recipients.extend(order.technicians.values_list("email", flat=True))
    transaction.on_commit(lambda: _send_email(subject, body, recipients))


def can_manage_work_order(user, order):
    return has_role(user, CHIEF) or (
        has_role(user, SUPERVISOR) and order.supervisors.filter(pk=user.pk).exists()
    )


def _require_work_order_manager(user, order):
    if not can_manage_work_order(user, order):
        raise PermissionDenied(
            "Solo el jefe o un supervisor asignado puede actualizar esta orden."
        )


def can_manage_material_stock(user):
    return has_role(user, CHIEF) or has_role(user, STOREKEEPER)


def can_request_work_order_materials(user, order):
    return has_role(user, CHIEF) or (
        order.supervisors.filter(pk=user.pk).exists()
        or order.technicians.filter(pk=user.pk).exists()
    )


def receive_material_stock(*, actor, material, quantity, notes=""):
    if not can_manage_material_stock(actor):
        raise PermissionDenied("Solo el jefe o un pañolero puede ingresar stock.")
    if quantity <= 0:
        raise ValidationError("La cantidad recibida debe ser mayor que cero.")
    with transaction.atomic():
        locked_material = Material.objects.select_for_update().get(pk=material.pk)
        locked_material.quantity_in_stock += quantity
        locked_material.save(update_fields=["quantity_in_stock"])
        movement = MaterialStockMovement.objects.create(
            material=locked_material,
            kind=MaterialStockMovement.Kind.RECEIPT,
            quantity=quantity,
            notes=notes.strip(),
            actor=actor,
        )
    return movement


def create_material_request(*, order, actor, material, quantity, notes=""):
    if not can_request_work_order_materials(actor, order):
        raise PermissionDenied(
            "Solo el jefe o personal asignado a esta orden puede pedir materiales."
        )
    if quantity <= 0:
        raise ValidationError("La cantidad solicitada debe ser mayor que cero.")
    if not material.pk:
        raise ValidationError("Selecciona un material válido.")
    return WorkOrderMaterialRequest.objects.create(
        work_order=order,
        material=material,
        requested_quantity=quantity,
        notes=notes.strip(),
        requested_by=actor,
    )


def deliver_material_request(*, request, actor, quantity, notes=""):
    if not can_manage_material_stock(actor):
        raise PermissionDenied("Solo el jefe o un pañolero puede entregar materiales.")
    if quantity <= 0:
        raise ValidationError("La cantidad a entregar debe ser mayor que cero.")
    with transaction.atomic():
        locked_request = (
            WorkOrderMaterialRequest.objects.select_for_update()
            .select_related("material")
            .get(pk=request.pk)
        )
        if locked_request.status == WorkOrderMaterialRequest.Status.FULFILLED:
            raise ValidationError("Este pedido ya fue entregado por completo.")
        if quantity > locked_request.remaining_quantity:
            raise ValidationError("La cantidad supera lo que falta entregar.")
        material = Material.objects.select_for_update().get(
            pk=locked_request.material_id
        )
        if quantity > material.quantity_in_stock:
            raise ValidationError("No hay stock suficiente para esa entrega.")
        material.quantity_in_stock -= quantity
        material.save(update_fields=["quantity_in_stock"])
        locked_request.delivered_quantity += quantity
        locked_request.status = (
            WorkOrderMaterialRequest.Status.FULFILLED
            if locked_request.delivered_quantity == locked_request.requested_quantity
            else WorkOrderMaterialRequest.Status.PARTIAL
        )
        locked_request.save(
            update_fields=["delivered_quantity", "status", "updated_at"]
        )
        movement = MaterialStockMovement.objects.create(
            material=material,
            kind=MaterialStockMovement.Kind.DELIVERY,
            quantity=quantity,
            work_order=locked_request.work_order,
            request=locked_request,
            notes=notes.strip(),
            actor=actor,
        )
    return movement


def create_work_order(*, actor, values):
    if not has_role(actor, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear órdenes.")
    values = dict(values)
    supervisors = values.pop("supervisors")
    assets = values.pop("assets", [])
    if not supervisors or any(not has_role(user, SUPERVISOR) for user in supervisors):
        raise ValidationError("Debes asignar al menos un usuario con rol Supervisor.")
    if not values.get("equipment") and not assets:
        raise ValidationError("Vincula al menos un activo del árbol o un equipo.")
    if any(not isinstance(asset, Asset) or not asset.pk for asset in assets):
        raise ValidationError("La lista contiene un activo no válido.")
    order = WorkOrder(**values, created_by=actor)
    order.full_clean()
    with transaction.atomic():
        order.save()
        order.supervisors.set(supervisors)
        order.assets.set(assets)
        _record_history(
            order,
            actor,
            "Orden creada",
            new_values=work_order_snapshot(order),
        )
        _notify_order(order, f"Nueva orden: {order.title}", f"Se creó la orden {order}.")
    return order


def update_work_order_status(*, order, actor, new_status):
    _require_work_order_manager(actor, order)
    if new_status not in STATUS_TRANSITIONS[order.status]:
        raise ValidationError("La transición de estado solicitada no está permitida.")
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        _require_work_order_manager(actor, locked)
        if new_status not in STATUS_TRANSITIONS[locked.status]:
            raise ValidationError("La orden cambió y esa transición ya no está permitida.")
        old_status = locked.status
        locked.status = new_status
        locked.save(update_fields=["status", "updated_at"])
        _record_history(
            locked,
            actor,
            "Estado actualizado",
            old_values={"status": old_status},
            new_values={"status": new_status},
        )
        _notify_order(
            locked,
            f"Estado actualizado: {locked.title}",
            f"La orden {locked} cambió de {old_status} a {new_status}.",
        )
    return locked


def mark_materials_received(*, order, actor):
    _require_work_order_manager(actor, order)
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        _require_work_order_manager(actor, locked)
        if locked.materials_received_at is not None:
            raise ValidationError("La recepción de materiales ya fue registrada.")
        old_values = {"materials_received": False}
        locked.materials_received_at = timezone.now()
        locked.materials_received_by = actor
        locked.save(
            update_fields=[
                "materials_received_at",
                "materials_received_by",
                "updated_at",
            ]
        )
        _record_history(
            locked,
            actor,
            "Materiales recibidos",
            old_values=old_values,
            new_values={
                "materials_received": True,
                "received_by": actor.get_username(),
                "received_at": locked.materials_received_at.isoformat(),
            },
        )
        _notify_order(
            locked,
            f"Materiales recibidos: {locked.title}",
            f"{actor.get_username()} confirmó la recepción de materiales para {locked}.",
        )
    return locked


def create_diary_entry(*, order, actor, entry_date, description, operators):
    _require_work_order_manager(actor, order)
    if entry_date > timezone.localdate():
        raise ValidationError("La fecha del trabajo no puede estar en el futuro.")
    if not description.strip():
        raise ValidationError("Describe el trabajo realizado.")
    if not operators:
        raise ValidationError("Selecciona al menos un operario participante.")
    invalid_operators = [
        operator
        for operator in operators
        if not operator.is_active
        or not any(has_role(operator, role) for role in WORK_ORDER_STAFF_ROLES)
    ]
    if invalid_operators:
        raise ValidationError(
            "Todos los participantes deben ser Operarios o Técnicos activos."
        )
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        _require_work_order_manager(actor, locked)
        entry = WorkOrderDiaryEntry.objects.create(
            work_order=locked,
            entry_date=entry_date,
            description=description.strip(),
            created_by=actor,
        )
        entry.operators.set(operators)
        _record_history(
            locked,
            actor,
            "Trabajo agregado al diario",
            new_values={
                "diary_entry_id": entry.pk,
                "entry_date": entry.entry_date.isoformat(),
                "description": entry.description,
                "operators": sorted(
                    operator.get_username() for operator in operators
                ),
            },
        )
    return entry


def assign_technicians(*, order, actor, technicians):
    if not has_role(actor, SUPERVISOR) or not order.supervisors.filter(pk=actor.pk).exists():
        raise PermissionDenied(
            "Solo un supervisor asignado puede asignar operarios y técnicos."
        )
    invalid = [
        user
        for user in technicians
        if not any(has_role(user, role) for role in WORK_ORDER_STAFF_ROLES)
    ]
    if invalid:
        raise ValidationError(
            "Todos los usuarios asignados deben ser Operarios o Técnicos."
        )
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        if not locked.supervisors.filter(pk=actor.pk).exists():
            raise PermissionDenied("Ya no eres supervisor asignado a esta orden.")
        old_ids = _id_list(locked.technicians)
        new_ids = sorted(user.pk for user in technicians)
        if old_ids != new_ids:
            locked.technicians.set(technicians)
            _record_history(
                locked,
                actor,
                "Asignación de personal técnico actualizada",
                old_values={"technicians": old_ids},
                new_values={"technicians": new_ids},
            )
            _notify_order(
                locked,
                f"Asignación de personal técnico actualizada: {locked.title}",
                f"Se actualizó el personal técnico asignado a la orden {locked}.",
            )
    return locked


def submit_change_request(*, order, actor, proposed_values, reason):
    if not has_role(actor, SUPERVISOR) or not order.supervisors.filter(pk=actor.pk).exists():
        raise PermissionDenied("Solo un supervisor asignado puede solicitar cambios.")
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        if not locked.supervisors.filter(pk=actor.pk).exists():
            raise PermissionDenied("Ya no eres supervisor asignado a esta orden.")
        if locked.change_requests.filter(status=WorkOrderChangeRequest.Status.PENDING).exists():
            raise ValidationError("Ya existe una solicitud pendiente para esta orden.")
        baseline = approval_snapshot(locked)
        normalized = {
            "start_date": proposed_values["start_date"].isoformat(),
            "estimated_days": proposed_values["estimated_days"],
            "equipment": proposed_values["equipment"].pk
            if proposed_values["equipment"]
            else None,
            "machine_part": proposed_values["machine_part"].pk
            if proposed_values["machine_part"]
            else None,
            "supervisors": sorted(user.pk for user in proposed_values["supervisors"]),
            "assets": sorted(asset.pk for asset in proposed_values["assets"]),
        }
        if normalized == baseline:
            raise ValidationError("La solicitud no contiene cambios.")
        part = proposed_values["machine_part"]
        equipment = proposed_values["equipment"]
        assets = proposed_values["assets"]
        if not equipment and not assets:
            raise ValidationError("Vincula al menos un activo del árbol o un equipo.")
        if part and (not equipment or part.equipment_id != equipment.pk):
            raise ValidationError("La parte seleccionada no pertenece al equipo indicado.")
        request = WorkOrderChangeRequest.objects.create(
            work_order=locked,
            requested_by=actor,
            proposed_values=normalized,
            original_values=baseline,
            reason=reason,
        )
        _record_history(
            locked,
            actor,
            "Solicitud de cambio creada",
            new_values={"request_id": request.pk, "proposed_values": normalized},
        )
        chiefs = (
            get_user_model()
            .objects.filter(Q(groups__name=CHIEF) | Q(is_superuser=True))
            .values_list("email", flat=True)
            .distinct()
        )
        recipients = list(chiefs)
        transaction.on_commit(
            lambda: _send_email(
                f"Aprobación requerida: {locked.title}",
                f"{actor.get_username()} solicitó cambios para la orden {locked}.",
                recipients,
            )
        )
    return request


def resolve_change_request(*, request, actor, approve, comment=""):
    if not has_role(actor, CHIEF):
        raise PermissionDenied("Solo el jefe puede resolver solicitudes.")
    with transaction.atomic():
        locked_request = (
            WorkOrderChangeRequest.objects.select_for_update()
            .select_related("work_order")
            .get(pk=request.pk)
        )
        if locked_request.status != WorkOrderChangeRequest.Status.PENDING:
            raise ValidationError("La solicitud ya fue resuelta.")
        order = WorkOrder.objects.select_for_update().get(pk=locked_request.work_order_id)
        old_values = approval_snapshot(order)
        if approve:
            if old_values != locked_request.original_values:
                raise ValidationError(
                    "La orden cambió desde que se solicitó la modificación. "
                    "Revisa los datos y crea una nueva solicitud."
                )
            values = locked_request.proposed_values
            try:
                equipment = (
                    Equipment.objects.get(pk=values["equipment"])
                    if values["equipment"]
                    else None
                )
                part = (
                    MachinePart.objects.get(pk=values["machine_part"])
                    if values["machine_part"]
                    else None
                )
                linked_assets = list(Asset.objects.filter(pk__in=values["assets"]))
            except (Equipment.DoesNotExist, MachinePart.DoesNotExist) as error:
                raise ValidationError(
                    "El equipo o la parte propuestos ya no están disponibles."
                ) from error
            if len(linked_assets) != len(set(values["assets"])):
                raise ValidationError("Uno o más activos propuestos ya no existen.")
            if not equipment and not linked_assets:
                raise ValidationError("La solicitud debe conservar un activo o equipo vinculado.")
            if part and (not equipment or part.equipment_id != equipment.pk):
                raise ValidationError("La parte propuesta no pertenece al equipo propuesto.")
            user_model = get_user_model()
            supervisors = list(user_model.objects.filter(pk__in=values["supervisors"]))
            if len(supervisors) != len(set(values["supervisors"])) or any(
                not has_role(user, SUPERVISOR) for user in supervisors
            ):
                raise ValidationError("La lista propuesta contiene supervisores no válidos.")
            order.start_date = date.fromisoformat(values["start_date"])
            order.estimated_days = values["estimated_days"]
            order.equipment = equipment
            order.machine_part = part
            order.full_clean()
            order.save()
            order.supervisors.set(supervisors)
            order.assets.set(linked_assets)
            locked_request.status = WorkOrderChangeRequest.Status.APPROVED
            action = "Solicitud de cambio aprobada"
        else:
            locked_request.status = WorkOrderChangeRequest.Status.REJECTED
            action = "Solicitud de cambio rechazada"
        locked_request.resolution_comment = comment
        locked_request.resolved_by = actor
        locked_request.resolved_at = timezone.now()
        locked_request.save(
            update_fields=["status", "resolution_comment", "resolved_by", "resolved_at"]
        )
        _record_history(
            order,
            actor,
            action,
            old_values=old_values,
            new_values={
                "request_id": locked_request.pk,
                "status": locked_request.status,
                "values": approval_snapshot(order) if approve else {},
                "comment": comment,
            },
        )
        _notify_order(
            order,
            f"Solicitud {locked_request.get_status_display().lower()}: {order.title}",
            f"La solicitud #{locked_request.pk} fue {locked_request.get_status_display().lower()}.",
        )
        requester_email = locked_request.requested_by.email
        transaction.on_commit(
            lambda: _send_email(
                f"Solicitud {locked_request.get_status_display().lower()}: {order.title}",
                f"La solicitud #{locked_request.pk} fue "
                f"{locked_request.get_status_display().lower()}. {comment}",
                [requester_email],
            )
        )
    return locked_request

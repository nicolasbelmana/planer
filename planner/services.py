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

from .models import Equipment, MachinePart, WorkOrder, WorkOrderChangeRequest, WorkOrderHistory
from .roles import CHIEF, SUPERVISOR, TECHNICIAN, has_role

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


def create_work_order(*, actor, values):
    if not has_role(actor, CHIEF):
        raise PermissionDenied("Solo el jefe puede crear órdenes.")
    values = dict(values)
    supervisors = values.pop("supervisors")
    if not supervisors or any(not has_role(user, SUPERVISOR) for user in supervisors):
        raise ValidationError("Debes asignar al menos un usuario con rol Supervisor.")
    order = WorkOrder(**values, created_by=actor)
    order.full_clean()
    with transaction.atomic():
        order.save()
        order.supervisors.set(supervisors)
        _record_history(
            order,
            actor,
            "Orden creada",
            new_values=work_order_snapshot(order),
        )
        _notify_order(order, f"Nueva orden: {order.title}", f"Se creó la orden {order}.")
    return order


def update_work_order_status(*, order, actor, new_status):
    if not has_role(actor, SUPERVISOR) or not order.supervisors.filter(pk=actor.pk).exists():
        raise PermissionDenied("Solo un supervisor asignado puede cambiar el estado.")
    if new_status not in STATUS_TRANSITIONS[order.status]:
        raise ValidationError("La transición de estado solicitada no está permitida.")
    with transaction.atomic():
        locked = WorkOrder.objects.select_for_update().get(pk=order.pk)
        if not locked.supervisors.filter(pk=actor.pk).exists():
            raise PermissionDenied("Ya no eres supervisor asignado a esta orden.")
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


def assign_technicians(*, order, actor, technicians):
    if not has_role(actor, SUPERVISOR) or not order.supervisors.filter(pk=actor.pk).exists():
        raise PermissionDenied("Solo un supervisor asignado puede asignar técnicos.")
    invalid = [user for user in technicians if not has_role(user, TECHNICIAN)]
    if invalid:
        raise ValidationError("Todos los usuarios asignados deben tener el rol Técnico.")
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
                "Asignación de técnicos actualizada",
                old_values={"technicians": old_ids},
                new_values={"technicians": new_ids},
            )
            _notify_order(
                locked,
                f"Asignación actualizada: {locked.title}",
                f"Se actualizaron los técnicos asignados a la orden {locked}.",
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
            "equipment": proposed_values["equipment"].pk,
            "machine_part": proposed_values["machine_part"].pk
            if proposed_values["machine_part"]
            else None,
            "supervisors": sorted(user.pk for user in proposed_values["supervisors"]),
        }
        if normalized == baseline:
            raise ValidationError("La solicitud no contiene cambios.")
        part = proposed_values["machine_part"]
        if part and part.equipment_id != proposed_values["equipment"].pk:
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
                equipment = Equipment.objects.get(pk=values["equipment"])
                part = (
                    MachinePart.objects.get(pk=values["machine_part"])
                    if values["machine_part"]
                    else None
                )
            except (Equipment.DoesNotExist, MachinePart.DoesNotExist) as error:
                raise ValidationError(
                    "El equipo o la parte propuestos ya no están disponibles."
                ) from error
            if part and part.equipment_id != equipment.pk:
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

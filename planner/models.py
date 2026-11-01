from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Equipment(models.Model):
    name = models.CharField("nombre", max_length=150, unique=True)
    description = models.TextField("descripción", blank=True)
    location = models.CharField("ubicación", max_length=200, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "equipo"
        verbose_name_plural = "equipos"

    def __str__(self):
        return self.name


class MachinePart(models.Model):
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="parts",
        verbose_name="equipo",
    )
    name = models.CharField("nombre", max_length=150)

    class Meta:
        ordering = ["equipment__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["equipment", "name"],
                name="unique_machine_part_per_equipment",
            )
        ]
        verbose_name = "parte de máquina"
        verbose_name_plural = "partes de máquina"

    def __str__(self):
        return f"{self.equipment}: {self.name}"


class WorkOrder(models.Model):
    class Status(models.TextChoices):
        PREPARATION = "PREPARATION", "En preparación"
        WAITING_MATERIALS = "WAITING_MATERIALS", "Esperando materiales"
        IN_PROGRESS = "IN_PROGRESS", "En curso"
        COMPLETED = "COMPLETED", "Finalizado"

    title = models.CharField("título", max_length=200)
    description = models.TextField("descripción", blank=True)
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.PROTECT,
        related_name="work_orders",
        verbose_name="equipo",
    )
    machine_part = models.ForeignKey(
        MachinePart,
        on_delete=models.PROTECT,
        related_name="work_orders",
        null=True,
        blank=True,
        verbose_name="parte",
    )
    supervisors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="supervised_work_orders",
        verbose_name="supervisores",
    )
    technicians = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="assigned_work_orders",
        blank=True,
        verbose_name="técnicos",
    )
    start_date = models.DateField("fecha de inicio")
    estimated_days = models.PositiveIntegerField(
        "días estimados",
        validators=[MinValueValidator(1)],
    )
    status = models.CharField(
        "estado",
        max_length=24,
        choices=Status.choices,
        default=Status.PREPARATION,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_work_orders",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("creada", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada", auto_now=True)

    class Meta:
        ordering = ["start_date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(estimated_days__gt=0),
                name="work_order_estimated_days_positive",
            )
        ]
        verbose_name = "orden de trabajo"
        verbose_name_plural = "órdenes de trabajo"

    def __str__(self):
        return f"OT-{self.pk}: {self.title}"

    @property
    def due_date(self):
        if not self.start_date or not self.estimated_days:
            return None
        return self.start_date + timedelta(days=self.estimated_days - 1)

    @property
    def is_overdue(self):
        return (
            self.status != self.Status.COMPLETED
            and self.due_date is not None
            and self.due_date < timezone.localdate()
        )

    def clean(self):
        super().clean()
        if self.estimated_days is not None and self.estimated_days < 1:
            raise ValidationError({"estimated_days": "Los días estimados deben ser mayores que cero."})
        if (
            self.machine_part_id
            and self.equipment_id
            and self.machine_part.equipment_id != self.equipment_id
        ):
            raise ValidationError(
                {"machine_part": "La parte seleccionada no pertenece al equipo indicado."}
            )


class WorkOrderChangeRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        APPROVED = "APPROVED", "Aprobada"
        REJECTED = "REJECTED", "Rechazada"

    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="change_requests",
        verbose_name="orden",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="work_order_change_requests",
        verbose_name="solicitada por",
    )
    proposed_values = models.JSONField("cambios propuestos")
    original_values = models.JSONField("valores al solicitar")
    reason = models.TextField("motivo")
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    resolution_comment = models.TextField("comentario de resolución", blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="resolved_work_order_change_requests",
        null=True,
        blank=True,
        verbose_name="resuelta por",
    )
    created_at = models.DateTimeField("solicitada", auto_now_add=True)
    resolved_at = models.DateTimeField("resuelta", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "solicitud de cambio"
        verbose_name_plural = "solicitudes de cambio"

    def __str__(self):
        return f"Solicitud #{self.pk} - {self.work_order}"


class WorkOrderHistory(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="orden",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="work_order_history_entries",
        verbose_name="usuario",
    )
    action = models.CharField("acción", max_length=100)
    old_values = models.JSONField("valores anteriores", default=dict, blank=True)
    new_values = models.JSONField("valores nuevos", default=dict, blank=True)
    created_at = models.DateTimeField("fecha", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "registro de historial"
        verbose_name_plural = "historial"

    def __str__(self):
        return f"{self.work_order}: {self.action}"

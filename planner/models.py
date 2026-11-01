from datetime import timedelta
import re

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


class AssetType(models.Model):
    name = models.CharField("tipo", max_length=80, unique=True)
    key = models.SlugField("clave", max_length=80, unique=True)
    specification_fields = models.JSONField(
        "campos técnicos",
        default=list,
        blank=True,
        help_text="Lista configurable de campos; cada elemento incluye key, label y opcionalmente unit.",
    )
    is_location = models.BooleanField(
        "puede representar una ubicación",
        default=False,
        help_text="Actívalo para niveles como planta, piso o sector.",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "tipo de activo"
        verbose_name_plural = "tipos de activo"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not isinstance(self.specification_fields, list):
            raise ValidationError({"specification_fields": "Debe ser una lista de campos."})
        keys = set()
        for field in self.specification_fields:
            if (
                not isinstance(field, dict)
                or not field.get("key")
                or not field.get("label")
                or not isinstance(field.get("key"), str)
                or not isinstance(field.get("label"), str)
                or not re.fullmatch(r"[a-z][a-z0-9_]*", field["key"])
            ):
                raise ValidationError(
                    {
                        "specification_fields": (
                            "Cada campo requiere una clave única en formato "
                            "minúsculas_sin_espacios y una etiqueta."
                        )
                    }
                )
            if field["key"] in keys:
                raise ValidationError(
                    {"specification_fields": "Las claves de campos técnicos deben ser únicas."}
                )
            keys.add(field["key"])


class Asset(models.Model):
    name = models.CharField("nombre", max_length=160)
    asset_type = models.ForeignKey(
        AssetType,
        on_delete=models.PROTECT,
        related_name="assets",
        verbose_name="tipo",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="children",
        null=True,
        blank=True,
        verbose_name="elemento padre",
    )
    location = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="located_assets",
        null=True,
        blank=True,
        verbose_name="ubicación / piso",
    )
    brand = models.CharField("marca", max_length=120, blank=True)
    model = models.CharField("modelo", max_length=120, blank=True)
    serial_number = models.CharField("número de serie", max_length=120, blank=True)
    specifications = models.JSONField("especificaciones", default=dict, blank=True)
    notes = models.TextField("notas", blank=True)
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="unique_asset_name_per_parent",
            )
        ]
        verbose_name = "activo"
        verbose_name_plural = "activos"

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "Un activo no puede ser su propio padre."})
        if self.location_id and self.location.asset_type.is_location is False:
            raise ValidationError(
                {"location": "La ubicación seleccionada debe ser un tipo de activo de ubicación."}
            )
        if self.parent_id and self.pk:
            ancestor = self.parent
            visited = set()
            while ancestor is not None:
                if ancestor.pk == self.pk:
                    raise ValidationError({"parent": "La jerarquía no puede contener ciclos."})
                if ancestor.pk in visited:
                    raise ValidationError({"parent": "La jerarquía existente contiene un ciclo."})
                visited.add(ancestor.pk)
                ancestor = ancestor.parent
        if not isinstance(self.specifications, dict):
            raise ValidationError({"specifications": "Las especificaciones deben ser un objeto."})
        allowed = {field["key"] for field in self.asset_type.specification_fields}
        unexpected = set(self.specifications) - allowed
        if unexpected:
            raise ValidationError(
                {"specifications": "Hay especificaciones que no corresponden al tipo de activo."}
            )

    @property
    def floor(self):
        current = self
        visited = set()
        while current is not None and current.pk not in visited:
            visited.add(current.pk)
            if current.asset_type.is_location:
                return current
            if current.location_id:
                return current.location
            current = current.parent
        return None


class AssetConnection(models.Model):
    class Kind(models.TextChoices):
        CONNECTED_TO = "CONNECTED_TO", "Conectado a"
        POWERED_BY = "POWERED_BY", "Alimentado por"
        FLOWS_TO = "FLOWS_TO", "Flujo hacia"

    source = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="outgoing_connections",
        verbose_name="origen",
    )
    target = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="incoming_connections",
        verbose_name="destino",
    )
    kind = models.CharField("relación", max_length=20, choices=Kind.choices)
    notes = models.CharField("notas", max_length=250, blank=True)
    created_at = models.DateTimeField("creada", auto_now_add=True)

    class Meta:
        ordering = ["kind", "target__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["source", "target", "kind"],
                name="unique_asset_connection",
            ),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("target")),
                name="asset_connection_not_self",
            ),
        ]
        verbose_name = "conexión entre activos"
        verbose_name_plural = "conexiones entre activos"

    def __str__(self):
        return f"{self.source} — {self.get_kind_display()} → {self.target}"

    def clean(self):
        super().clean()
        if self.source_id and self.target_id and self.source_id == self.target_id:
            raise ValidationError("Un activo no puede conectarse consigo mismo.")


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
        null=True,
        blank=True,
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
    assets = models.ManyToManyField(
        Asset,
        related_name="work_orders",
        blank=True,
        verbose_name="activos vinculados",
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
        verbose_name="operarios y técnicos",
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
    materials_received_at = models.DateTimeField(
        "materiales recibidos el",
        null=True,
        blank=True,
    )
    materials_received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="work_orders_with_materials_received",
        null=True,
        blank=True,
        verbose_name="materiales recibidos confirmados por",
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

    @property
    def display_equipment(self):
        names = [self.equipment.name] if self.equipment_id else []
        names.extend(asset.name for asset in self.assets.all())
        return ", ".join(names) or "—"

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


class WorkOrderDiaryEntry(models.Model):
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="diary_entries",
        verbose_name="orden",
    )
    entry_date = models.DateField("fecha del trabajo")
    description = models.TextField("trabajo realizado")
    operators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="work_order_diary_entries",
        verbose_name="operarios y técnicos participantes",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_work_order_diary_entries",
        verbose_name="registrado por",
    )
    created_at = models.DateTimeField("registrado", auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-created_at", "-pk"]
        verbose_name = "entrada de diario"
        verbose_name_plural = "diario de órdenes"

    def __str__(self):
        return f"{self.work_order} — {self.entry_date}"


class Material(models.Model):
    name = models.CharField("material", max_length=160, unique=True)
    unit = models.CharField("unidad", max_length=40)
    quantity_in_stock = models.DecimalField(
        "stock disponible",
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    minimum_stock = models.DecimalField(
        "stock mínimo",
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    created_at = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "material"
        verbose_name_plural = "materiales"

    def __str__(self):
        return f"{self.name} ({self.unit})"

    @property
    def is_low_stock(self):
        return self.quantity_in_stock <= self.minimum_stock


class WorkOrderMaterialRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pendiente"
        PARTIAL = "PARTIAL", "Entrega parcial"
        FULFILLED = "FULFILLED", "Entregado"

    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="material_requests",
        verbose_name="orden de trabajo",
    )
    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name="work_order_requests",
        verbose_name="material",
    )
    requested_quantity = models.DecimalField(
        "cantidad solicitada",
        max_digits=12,
        decimal_places=3,
    )
    delivered_quantity = models.DecimalField(
        "cantidad entregada",
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    notes = models.TextField("detalle", blank=True)
    status = models.CharField(
        "estado",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_work_order_materials",
        verbose_name="solicitado por",
    )
    created_at = models.DateTimeField("solicitado", auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(requested_quantity__gt=0),
                name="material_request_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(delivered_quantity__gte=0),
                name="material_request_delivered_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    delivered_quantity__lte=models.F("requested_quantity")
                ),
                name="material_request_delivery_not_over_requested",
            ),
        ]
        verbose_name = "pedido de material"
        verbose_name_plural = "pedidos de materiales"

    def __str__(self):
        return f"OT-{self.work_order_id}: {self.material} × {self.requested_quantity}"

    @property
    def remaining_quantity(self):
        return self.requested_quantity - self.delivered_quantity


class MaterialStockMovement(models.Model):
    class Kind(models.TextChoices):
        RECEIPT = "RECEIPT", "Ingreso"
        DELIVERY = "DELIVERY", "Entrega"

    material = models.ForeignKey(
        Material,
        on_delete=models.PROTECT,
        related_name="movements",
        verbose_name="material",
    )
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices)
    quantity = models.DecimalField("cantidad", max_digits=12, decimal_places=3)
    work_order = models.ForeignKey(
        WorkOrder,
        on_delete=models.PROTECT,
        related_name="material_movements",
        null=True,
        blank=True,
        verbose_name="orden de trabajo",
    )
    request = models.ForeignKey(
        WorkOrderMaterialRequest,
        on_delete=models.PROTECT,
        related_name="movements",
        null=True,
        blank=True,
        verbose_name="pedido",
    )
    notes = models.TextField("detalle", blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="material_stock_movements",
        verbose_name="registrado por",
    )
    created_at = models.DateTimeField("fecha", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="material_movement_quantity_positive",
            )
        ]
        verbose_name = "movimiento de stock"
        verbose_name_plural = "movimientos de stock"

    def __str__(self):
        return f"{self.get_kind_display()}: {self.material} × {self.quantity}"


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


class PreventiveMaintenancePlan(models.Model):
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="maintenance_plans",
        verbose_name="activo",
    )
    name = models.CharField("nombre del mantenimiento", max_length=160)
    instructions = models.TextField("instrucciones", blank=True)
    interval_days = models.PositiveIntegerField(
        "frecuencia en días corridos",
        validators=[MinValueValidator(1)],
    )
    next_due_date = models.DateField("próximo mantenimiento")
    reminder_days_before = models.PositiveIntegerField(
        "avisar con días de anticipación",
        default=7,
    )
    supervisors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="preventive_maintenance_plans",
        verbose_name="supervisores responsables",
    )
    is_active = models.BooleanField("activo", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_preventive_plans",
        verbose_name="creado por",
    )
    created_at = models.DateTimeField("creado", auto_now_add=True)

    class Meta:
        ordering = ["next_due_date", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(interval_days__gt=0),
                name="preventive_interval_positive",
            )
        ]
        verbose_name = "plan preventivo"
        verbose_name_plural = "planes preventivos"

    def __str__(self):
        return f"{self.name} — {self.asset}"

    def clean(self):
        super().clean()
        if self.interval_days is not None and self.interval_days < 1:
            raise ValidationError({"interval_days": "La frecuencia debe ser mayor que cero."})


class PreventiveMaintenanceRecord(models.Model):
    plan = models.ForeignKey(
        PreventiveMaintenancePlan,
        on_delete=models.PROTECT,
        related_name="records",
        verbose_name="plan",
    )
    performed_on = models.DateField("realizado el")
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="preventive_maintenance_records",
        verbose_name="realizado por",
    )
    notes = models.TextField("notas", blank=True)
    created_at = models.DateTimeField("registrado", auto_now_add=True)

    class Meta:
        ordering = ["-performed_on", "-pk"]
        verbose_name = "mantenimiento realizado"
        verbose_name_plural = "mantenimientos realizados"

    def __str__(self):
        return f"{self.plan} — {self.performed_on}"


class PreventiveMaintenanceReminder(models.Model):
    plan = models.ForeignKey(
        PreventiveMaintenancePlan,
        on_delete=models.PROTECT,
        related_name="reminders",
        verbose_name="plan",
    )
    due_date = models.DateField("fecha prevista")
    sent_at = models.DateTimeField("enviado", auto_now_add=True)
    recipients = models.JSONField("destinatarios", default=list)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "due_date"],
                name="unique_preventive_reminder_per_due_date",
            )
        ]
        verbose_name = "recordatorio preventivo enviado"
        verbose_name_plural = "recordatorios preventivos enviados"

    def __str__(self):
        return f"Recordatorio {self.plan} — {self.due_date}"

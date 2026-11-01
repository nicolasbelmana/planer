from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Equipment, MachinePart, Material, WorkOrder, WorkOrderMaterialRequest
from .models import (
    Asset,
    AssetConnection,
    AssetType,
    PreventiveMaintenancePlan,
    PreventiveMaintenanceRecord,
    WorkOrderDiaryEntry,
)
from .roles import (
    ROLE_NAMES,
    SUPERVISOR,
    WORK_ORDER_STAFF_ROLES,
)
from .services import STATUS_TRANSITIONS


class StaffUserCreationForm(UserCreationForm):
    role = forms.ChoiceField(label="Rol", choices=[(role, role) for role in ROLE_NAMES])

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email")
        labels = {
            "username": "Usuario",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo electrónico",
        }

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            role = self.cleaned_data["role"]
            group, _ = Group.objects.get_or_create(name=role)
            user.groups.set([group])
        return user


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ("name", "description", "location")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class MachinePartForm(forms.ModelForm):
    class Meta:
        model = MachinePart
        fields = ("equipment", "name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = Equipment.objects.order_by("name")


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = (
            "name",
            "asset_type",
            "parent",
            "location",
            "brand",
            "model",
            "serial_number",
            "notes",
        )
        labels = {
            "name": "Nombre",
            "asset_type": "Tipo de activo",
            "parent": "Elemento padre",
            "location": "Piso / ubicación",
            "brand": "Marca",
            "model": "Modelo",
            "serial_number": "Número de serie",
            "notes": "Notas",
        }
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset_type"].queryset = AssetType.objects.order_by("name")
        self.fields["parent"].queryset = Asset.objects.select_related("asset_type").order_by(
            "name"
        )
        self.fields["parent"].required = False
        self.fields["location"].queryset = Asset.objects.filter(
            asset_type__is_location=True
        ).order_by("name")
        self.fields["location"].required = False
        self.fields["parent"].help_text = "Deja vacío si es un activo raíz."
        self.fields["location"].help_text = (
            "Opcional si el piso ya está representado por la jerarquía."
        )
        self.specification_groups = []
        for asset_type in self.fields["asset_type"].queryset:
            group_fields = []
            for specification in asset_type.specification_fields:
                key = specification["key"]
                label = specification["label"]
                unit = specification.get("unit")
                if unit:
                    label = f"{label} ({unit})"
                field_name = f"spec__{asset_type.key}__{key}"
                self.fields[field_name] = forms.CharField(
                    label=label,
                    required=False,
                    initial=(self.instance.specifications or {}).get(key, "")
                    if self.instance.pk and self.instance.asset_type_id == asset_type.pk
                    else "",
                    max_length=250,
                )
                group_fields.append((field_name, self.fields[field_name]))
            if group_fields:
                self.specification_groups.append((asset_type, group_fields))

    def clean(self):
        cleaned = super().clean()
        parent = cleaned.get("parent")
        asset_type = cleaned.get("asset_type")
        if parent and self.instance.pk:
            ancestor = parent
            visited = set()
            while ancestor is not None and ancestor.pk not in visited:
                if ancestor.pk == self.instance.pk:
                    self.add_error("parent", "La jerarquía no puede contener ciclos.")
                    break
                visited.add(ancestor.pk)
                ancestor = ancestor.parent
        if asset_type and asset_type.is_location and cleaned.get("location"):
            cleaned["location"] = None
        if asset_type:
            cleaned["specifications"] = {
                specification["key"]: cleaned.get(
                    f"spec__{asset_type.key}__{specification['key']}", ""
                ).strip()
                for specification in asset_type.specification_fields
                if cleaned.get(
                    f"spec__{asset_type.key}__{specification['key']}", ""
                ).strip()
            }
        return cleaned

    def save(self, commit=True):
        asset = super().save(commit=False)
        asset.specifications = self.cleaned_data.get("specifications", {})
        if commit:
            asset.full_clean()
            asset.save()
        return asset


class AssetConnectionForm(forms.ModelForm):
    class Meta:
        model = AssetConnection
        fields = ("source", "kind", "target", "notes")
        labels = {
            "source": "Activo de origen",
            "kind": "Tipo de relación",
            "target": "Activo relacionado",
            "notes": "Notas",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assets = Asset.objects.select_related("asset_type").order_by("name")
        self.fields["source"].queryset = assets
        self.fields["target"].queryset = assets


class PreventiveMaintenancePlanForm(forms.ModelForm):
    class Meta:
        model = PreventiveMaintenancePlan
        fields = (
            "asset",
            "name",
            "instructions",
            "interval_days",
            "next_due_date",
            "reminder_days_before",
            "supervisors",
        )
        labels = {
            "asset": "Activo",
            "name": "Mantenimiento preventivo",
            "instructions": "Instrucciones",
            "interval_days": "Frecuencia (días corridos desde la última realización)",
            "next_due_date": "Primera / próxima fecha programada",
            "reminder_days_before": "Avisar con anticipación (días)",
            "supervisors": "Supervisores responsables",
        }
        widgets = {
            "instructions": forms.Textarea(attrs={"rows": 3}),
            "next_due_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset"].queryset = Asset.objects.select_related(
            "asset_type"
        ).order_by("name")
        self.fields["supervisors"].queryset = (
            get_user_model().objects.filter(groups__name=SUPERVISOR)
            .distinct()
            .order_by("username")
        )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("supervisors"):
            self.add_error("supervisors", "Asigna al menos un supervisor responsable.")
        return cleaned


class PreventiveMaintenanceRecordForm(forms.ModelForm):
    class Meta:
        model = PreventiveMaintenanceRecord
        fields = ("performed_on", "notes")
        labels = {"performed_on": "Realizado el", "notes": "Notas del mantenimiento"}
        widgets = {
            "performed_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class WorkOrderCreateForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = (
            "title",
            "description",
            "assets",
            "equipment",
            "machine_part",
            "start_date",
            "estimated_days",
            "supervisors",
        )
        labels = {
            "assets": "Activos vinculados (árbol)",
            "equipment": "Equipo simple (legado)",
            "machine_part": "Parte del equipo simple",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = Equipment.objects.order_by("name")
        self.fields["equipment"].required = False
        self.fields["assets"].queryset = Asset.objects.select_related(
            "asset_type"
        ).order_by("asset_type__name", "name")
        self.fields["assets"].required = False
        self.fields["assets"].help_text = (
            "Vincula uno o más activos del árbol; también puedes elegir un equipo legado."
        )
        self.fields["machine_part"].queryset = MachinePart.objects.select_related(
            "equipment"
        )
        self.fields["supervisors"].queryset = (
            self.fields["supervisors"].queryset.filter(groups__name=SUPERVISOR)
            .distinct()
            .order_by("username")
        )
        self.fields["supervisors"].help_text = "El jefe asigna uno o más supervisores."

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        machine_part = cleaned.get("machine_part")
        assets = cleaned.get("assets")
        if equipment and machine_part and machine_part.equipment_id != equipment.pk:
            self.add_error("machine_part", "La parte seleccionada no pertenece a ese equipo.")
        if machine_part and not equipment:
            self.add_error("machine_part", "Selecciona el equipo al que pertenece esta parte.")
        if not equipment and not assets:
            self.add_error("assets", "Vincula al menos un activo o selecciona un equipo.")
        if not cleaned.get("supervisors"):
            self.add_error("supervisors", "Asigna al menos un supervisor.")
        return cleaned


class StatusUpdateForm(forms.Form):
    status = forms.ChoiceField(label="Nuevo estado")

    def __init__(self, order, *args, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = STATUS_TRANSITIONS[order.status]
        self.fields["status"].choices = [
            (status, WorkOrder.Status(status).label) for status in sorted(allowed)
        ]
        if not allowed:
            self.fields["status"].choices = [("", "No hay transiciones disponibles")]
            self.fields["status"].disabled = True


class WorkOrderDiaryEntryForm(forms.ModelForm):
    class Meta:
        model = WorkOrderDiaryEntry
        fields = ("entry_date", "description", "operators")
        labels = {
            "entry_date": "Fecha del trabajo",
            "description": "Trabajo realizado",
            "operators": "Operarios y técnicos que realizaron la tarea",
        }
        widgets = {
            "entry_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Ej.: Se realizó limpieza del tablero de bombas.",
                }
            ),
            "operators": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["entry_date"].initial = timezone.localdate()
        self.fields["operators"].queryset = (
            get_user_model()
            .objects.filter(
                groups__name__in=WORK_ORDER_STAFF_ROLES,
                is_active=True,
            )
            .distinct()
            .order_by("username")
        )
        self.fields["operators"].help_text = (
            "Puedes seleccionar cualquier operario o técnico activo."
        )

    def clean_entry_date(self):
        entry_date = self.cleaned_data["entry_date"]
        if entry_date > timezone.localdate():
            raise ValidationError("La fecha del trabajo no puede estar en el futuro.")
        return entry_date


class TechnicianAssignmentForm(forms.Form):
    technicians = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        label="Operarios y técnicos asignados",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technicians"].queryset = (
            get_user_model().objects.filter(
                groups__name__in=WORK_ORDER_STAFF_ROLES
            )
            .distinct()
            .order_by("username")
        )


class MaterialCatalogForm(forms.ModelForm):
    minimum_stock = forms.DecimalField(
        label="Avisar cuando el stock llegue a",
        min_value=0,
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.001"}),
    )

    class Meta:
        model = Material
        fields = ("name", "unit", "minimum_stock")
        labels = {
            "name": "Material",
            "unit": "Unidad (unidad, kg, m, L, etc.)",
            "minimum_stock": "Avisar cuando el stock llegue a",
        }
        widgets = {
            "minimum_stock": forms.NumberInput(attrs={"min": "0", "step": "0.001"}),
        }


class MaterialStockReceiptForm(forms.Form):
    material = forms.ModelChoiceField(
        queryset=Material.objects.none(),
        label="Material",
    )
    quantity = forms.DecimalField(
        label="Cantidad recibida",
        min_value=0.001,
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
    )
    notes = forms.CharField(
        label="Detalle del ingreso",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.order_by("name")


class WorkOrderMaterialRequestForm(forms.ModelForm):
    requested_quantity = forms.DecimalField(
        label="Cantidad",
        min_value=0.001,
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
    )

    class Meta:
        model = WorkOrderMaterialRequest
        fields = ("material", "requested_quantity", "notes")
        labels = {
            "material": "Material solicitado",
            "requested_quantity": "Cantidad",
            "notes": "Detalle / uso previsto",
        }
        widgets = {
            "requested_quantity": forms.NumberInput(
                attrs={"min": "0.001", "step": "0.001"}
            ),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.order_by("name")


class MaterialDeliveryForm(forms.Form):
    quantity = forms.DecimalField(
        label="Cantidad a entregar",
        min_value=0.001,
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(attrs={"min": "0.001", "step": "0.001"}),
    )
    notes = forms.CharField(
        label="Detalle de entrega",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 2}),
    )


class ChangeRequestForm(forms.Form):
    start_date = forms.DateField(
        label="Nueva fecha de inicio",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    estimated_days = forms.IntegerField(label="Días corridos estimados", min_value=1)
    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.none(),
        label="Equipo simple (legado)",
        required=False,
    )
    assets = forms.ModelMultipleChoiceField(
        queryset=Asset.objects.none(),
        label="Activos vinculados",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 8}),
    )
    machine_part = forms.ModelChoiceField(
        queryset=MachinePart.objects.none(),
        label="Parte del equipo simple",
        required=False,
    )
    supervisors = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        label="Supervisores",
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )
    reason = forms.CharField(
        label="Motivo de la solicitud",
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=2000,
    )

    def __init__(self, order, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        self.fields["start_date"].initial = order.start_date
        self.fields["estimated_days"].initial = order.estimated_days
        self.fields["equipment"].queryset = Equipment.objects.order_by("name")
        self.fields["assets"].queryset = Asset.objects.select_related(
            "asset_type"
        ).order_by("asset_type__name", "name")
        self.fields["machine_part"].queryset = MachinePart.objects.select_related(
            "equipment"
        )
        self.fields["machine_part"].initial = order.machine_part_id
        self.fields["equipment"].initial = order.equipment_id
        self.fields["assets"].initial = order.assets.values_list("pk", flat=True)
        self.fields["supervisors"].queryset = (
            get_user_model().objects.filter(groups__name=SUPERVISOR)
            .distinct()
            .order_by("username")
        )
        self.fields["supervisors"].initial = order.supervisors.values_list("pk", flat=True)

    def clean(self):
        cleaned = super().clean()
        equipment = cleaned.get("equipment")
        part = cleaned.get("machine_part")
        if equipment and part and part.equipment_id != equipment.pk:
            self.add_error("machine_part", "La parte seleccionada no pertenece a ese equipo.")
        if part and not equipment:
            self.add_error("machine_part", "Selecciona el equipo al que pertenece esta parte.")
        if not equipment and not cleaned.get("assets"):
            self.add_error("assets", "Vincula al menos un activo o selecciona un equipo.")
        if not cleaned.get("supervisors"):
            self.add_error("supervisors", "Debe quedar al menos un supervisor asignado.")
        return cleaned

    def proposed_values(self):
        if not self.is_valid():
            raise ValidationError("No se pueden obtener los cambios de un formulario inválido.")
        return {
            "start_date": self.cleaned_data["start_date"],
            "estimated_days": self.cleaned_data["estimated_days"],
            "equipment": self.cleaned_data["equipment"],
            "machine_part": self.cleaned_data["machine_part"],
            "assets": list(self.cleaned_data["assets"]),
            "supervisors": list(self.cleaned_data["supervisors"]),
        }


class ChangeRequestResolutionForm(forms.Form):
    decision = forms.ChoiceField(
        label="Decisión",
        choices=(("approve", "Aprobar"), ("reject", "Rechazar")),
    )
    comment = forms.CharField(
        label="Comentario",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=2000,
    )

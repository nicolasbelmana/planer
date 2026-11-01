from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from .models import Equipment, MachinePart, WorkOrder
from .roles import SUPERVISOR, TECHNICIAN
from .services import STATUS_TRANSITIONS


class WorkOrderCreateForm(forms.ModelForm):
    class Meta:
        model = WorkOrder
        fields = (
            "title",
            "description",
            "equipment",
            "machine_part",
            "start_date",
            "estimated_days",
            "supervisors",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["equipment"].queryset = Equipment.objects.order_by("name")
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
        if equipment and machine_part and machine_part.equipment_id != equipment.pk:
            self.add_error("machine_part", "La parte seleccionada no pertenece a ese equipo.")
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


class TechnicianAssignmentForm(forms.Form):
    technicians = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        label="Técnicos asignados",
        required=False,
        widget=forms.SelectMultiple(attrs={"size": 6}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technicians"].queryset = (
            get_user_model().objects.filter(
                groups__name=TECHNICIAN
            )
            .distinct()
            .order_by("username")
        )


class ChangeRequestForm(forms.Form):
    start_date = forms.DateField(
        label="Nueva fecha de inicio",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    estimated_days = forms.IntegerField(label="Días corridos estimados", min_value=1)
    equipment = forms.ModelChoiceField(
        queryset=Equipment.objects.none(),
        label="Equipo",
    )
    machine_part = forms.ModelChoiceField(
        queryset=MachinePart.objects.none(),
        label="Parte",
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
        self.fields["machine_part"].queryset = MachinePart.objects.select_related(
            "equipment"
        )
        self.fields["machine_part"].initial = order.machine_part_id
        self.fields["equipment"].initial = order.equipment_id
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

from django.core.management.base import BaseCommand

from planner.models import AssetType


ASSET_TYPES = (
    {
        "key": "planta",
        "name": "Planta",
        "is_location": True,
        "specification_fields": [],
    },
    {
        "key": "piso",
        "name": "Piso",
        "is_location": True,
        "specification_fields": [],
    },
    {
        "key": "sistema",
        "name": "Sistema",
        "is_location": False,
        "specification_fields": [],
    },
    {
        "key": "tablero",
        "name": "Tablero",
        "is_location": False,
        "specification_fields": [
            {"key": "voltage", "label": "Tensión nominal", "unit": "V"},
            {"key": "current", "label": "Corriente nominal", "unit": "A"},
            {"key": "protection", "label": "Grado de protección"},
        ],
    },
    {
        "key": "bomba",
        "name": "Bomba",
        "is_location": False,
        "specification_fields": [
            {"key": "flow", "label": "Caudal", "unit": "m³/h"},
            {"key": "head", "label": "Altura manométrica", "unit": "m"},
            {"key": "pressure", "label": "Presión nominal", "unit": "bar"},
        ],
    },
    {
        "key": "motor",
        "name": "Motor",
        "is_location": False,
        "specification_fields": [
            {"key": "power", "label": "Potencia", "unit": "kW"},
            {"key": "voltage", "label": "Tensión", "unit": "V"},
            {"key": "rpm", "label": "Velocidad", "unit": "rpm"},
            {"key": "current", "label": "Corriente", "unit": "A"},
        ],
    },
    {
        "key": "caneria",
        "name": "Cañería",
        "is_location": False,
        "specification_fields": [
            {"key": "material", "label": "Material"},
            {"key": "diameter", "label": "Diámetro", "unit": "mm"},
            {"key": "pressure", "label": "Presión de trabajo", "unit": "bar"},
            {"key": "fluid", "label": "Fluido"},
        ],
    },
    {
        "key": "valvula",
        "name": "Válvula",
        "is_location": False,
        "specification_fields": [
            {"key": "valve_type", "label": "Tipo"},
            {"key": "material", "label": "Material"},
            {"key": "diameter", "label": "Diámetro", "unit": "mm"},
            {"key": "pressure", "label": "Presión nominal", "unit": "bar"},
        ],
    },
    {
        "key": "parte",
        "name": "Parte",
        "is_location": False,
        "specification_fields": [
            {"key": "part_number", "label": "Número de parte"},
            {"key": "material", "label": "Material"},
        ],
    },
    {
        "key": "componente",
        "name": "Componente",
        "is_location": False,
        "specification_fields": [
            {"key": "part_number", "label": "Número de parte"},
            {"key": "rating", "label": "Valor / capacidad"},
            {"key": "unit", "label": "Unidad"},
        ],
    },
)


class Command(BaseCommand):
    help = "Crea los tipos iniciales de activos con sus campos técnicos."

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0
        for values in ASSET_TYPES:
            asset_type, created = AssetType.objects.get_or_create(
                key=values["key"],
                defaults=values,
            )
            if created:
                created_count += 1
                self.stdout.write(f"Creado: {asset_type.name}")
                continue
            updated = False
            for field in ("name", "is_location"):
                if getattr(asset_type, field) != values[field]:
                    setattr(asset_type, field, values[field])
                    updated = True
            if not asset_type.specification_fields:
                asset_type.specification_fields = values["specification_fields"]
                updated = True
            if updated:
                asset_type.full_clean()
                asset_type.save()
                updated_count += 1
                self.stdout.write(f"Actualizado: {asset_type.name}")
            else:
                self.stdout.write(f"Ya existe: {asset_type.name}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Tipos de activo listos. Nuevos: {created_count}; actualizados: {updated_count}."
            )
        )

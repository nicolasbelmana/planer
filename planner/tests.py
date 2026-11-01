from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch

from .models import (
    Asset,
    AssetConnection,
    AssetType,
    Equipment,
    MachinePart,
    Material,
    MaterialStockMovement,
    PreventiveMaintenancePlan,
    PreventiveMaintenanceRecord,
    PreventiveMaintenanceReminder,
    WorkOrder,
    WorkOrderChangeRequest,
    WorkOrderDiaryEntry,
    WorkOrderMaterialRequest,
)
from .roles import CHIEF, OPERATOR, STOREKEEPER, SUPERVISOR, TECHNICIAN
from .services import (
    assign_technicians,
    create_material_request,
    create_work_order,
    deliver_material_request,
    receive_material_stock,
    resolve_change_request,
    submit_change_request,
    update_work_order_status,
)

User = get_user_model()


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WorkOrderWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chief_group = Group.objects.create(name=CHIEF)
        cls.supervisor_group = Group.objects.create(name=SUPERVISOR)
        cls.operator_group = Group.objects.create(name=OPERATOR)
        cls.technician_group = Group.objects.create(name=TECHNICIAN)
        cls.storekeeper_group = Group.objects.create(name=STOREKEEPER)
        cls.chief = User.objects.create_user(
            username="chief",
            password="test-password",
            email="chief@example.test",
        )
        cls.chief.groups.add(cls.chief_group)
        cls.supervisor = User.objects.create_user(
            username="supervisor",
            password="test-password",
            email="supervisor@example.test",
        )
        cls.supervisor.groups.add(cls.supervisor_group)
        cls.other_supervisor = User.objects.create_user(
            username="other-supervisor",
            password="test-password",
        )
        cls.other_supervisor.groups.add(cls.supervisor_group)
        cls.technician = User.objects.create_user(
            username="technician",
            password="test-password",
        )
        cls.technician.groups.add(cls.operator_group)
        cls.technical_staff = User.objects.create_user(
            username="technical-staff",
            password="test",
        )
        cls.technical_staff.groups.add(cls.technician_group)
        cls.storekeeper = User.objects.create_user(
            username="storekeeper",
            password="test",
        )
        cls.storekeeper.groups.add(cls.storekeeper_group)
        cls.equipment = Equipment.objects.create(name="Compresor")
        cls.other_equipment = Equipment.objects.create(name="Bomba")
        cls.part = MachinePart.objects.create(equipment=cls.equipment, name="Motor")
        cls.other_part = MachinePart.objects.create(
            equipment=cls.other_equipment,
            name="Impulsor",
        )

    def create_order(self, **overrides):
        values = {
            "title": "Mantenimiento preventivo",
            "description": "Revisión general",
            "equipment": self.equipment,
            "machine_part": self.part,
            "start_date": date(2026, 10, 1),
            "estimated_days": 5,
            "supervisors": [self.supervisor],
            "assets": [],
        }
        values.update(overrides)
        return create_work_order(actor=self.chief, values=values)

    def change_values(self, order, **overrides):
        values = {
            "start_date": date(2026, 10, 10),
            "estimated_days": 7,
            "equipment": self.other_equipment,
            "machine_part": self.other_part,
            "supervisors": [self.other_supervisor],
            "assets": [],
        }
        values.update(overrides)
        return values

    def test_deadline_counts_start_date_as_first_day(self):
        order = self.create_order(start_date=date(2026, 10, 1), estimated_days=5)

        self.assertEqual(order.due_date, date(2026, 10, 5))

    @patch("planner.models.timezone.localdate", return_value=date(2026, 10, 7))
    def test_overdue_is_independent_of_operational_status(self, _localdate):
        order = self.create_order(start_date=date(2026, 10, 1), estimated_days=5)

        self.assertEqual(order.status, WorkOrder.Status.PREPARATION)
        self.assertTrue(order.is_overdue)
        order.status = WorkOrder.Status.COMPLETED
        self.assertFalse(order.is_overdue)

    def test_work_order_rejects_part_from_another_equipment(self):
        order = WorkOrder(
            title="Trabajo",
            equipment=self.equipment,
            machine_part=self.other_part,
            start_date=date(2026, 10, 1),
            estimated_days=2,
            created_by=self.chief,
        )

        with self.assertRaises(ValidationError):
            order.full_clean()

    def test_status_transitions_are_sequential_and_audited(self):
        order = self.create_order()
        with self.assertRaises(ValidationError):
            update_work_order_status(
                order=order,
                actor=self.supervisor,
                new_status=WorkOrder.Status.COMPLETED,
            )

        order = update_work_order_status(
            order=order,
            actor=self.supervisor,
            new_status=WorkOrder.Status.IN_PROGRESS,
        )

        self.assertEqual(order.status, WorkOrder.Status.IN_PROGRESS)
        self.assertEqual(order.history.count(), 2)
        self.assertEqual(order.history.first().action, "Estado actualizado")

    def test_chief_can_update_status_on_any_order(self):
        order = self.create_order()

        updated = update_work_order_status(
            order=order,
            actor=self.chief,
            new_status=WorkOrder.Status.IN_PROGRESS,
        )

        self.assertEqual(updated.status, WorkOrder.Status.IN_PROGRESS)
        self.assertEqual(updated.history.first().actor, self.chief)

    def test_chief_can_update_status_from_order_detail(self):
        order = self.create_order()
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("work-order-status", args=[order.pk]),
            {"status": WorkOrder.Status.IN_PROGRESS},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrder.Status.IN_PROGRESS)

    def test_material_receipt_is_independent_and_audited(self):
        order = self.create_order()
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("work-order-materials-received", args=[order.pk])
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrder.Status.PREPARATION)
        self.assertIsNotNone(order.materials_received_at)
        self.assertEqual(order.materials_received_by, self.supervisor)
        self.assertEqual(order.history.first().action, "Materiales recibidos")
        self.assertEqual(
            self.client.post(
                reverse("work-order-materials-received", args=[order.pk])
            ).status_code,
            302,
        )
        self.assertEqual(order.history.count(), 2)

    def test_supervisor_can_add_diary_entry_with_any_active_operator(self):
        order = self.create_order()
        other_operator = User.objects.create_user(username="other-operator", password="test")
        other_operator.groups.add(self.technician_group)
        self.client.force_login(self.supervisor)
        entry_date = timezone.localdate().isoformat()

        response = self.client.post(
            reverse("work-order-diary-create", args=[order.pk]),
            {
                "entry_date": entry_date,
                "description": "Se realizó limpieza del tablero.",
                "operators": [other_operator.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        entry = WorkOrderDiaryEntry.objects.get(work_order=order)
        self.assertEqual(entry.entry_date, timezone.localdate())
        self.assertEqual(entry.description, "Se realizó limpieza del tablero.")
        self.assertEqual(entry.created_by, self.supervisor)
        self.assertQuerySetEqual(
            entry.operators.all(),
            [other_operator],
            transform=lambda user: user,
        )
        self.assertEqual(order.history.first().action, "Trabajo agregado al diario")
        self.assertContains(
            self.client.get(reverse("work-order-detail", args=[order.pk])),
            "Se realizó limpieza del tablero.",
        )
        pdf = self.client.get(reverse("work-order-pdf", args=[order.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_chief_can_register_materials_and_diary_for_any_order(self):
        order = self.create_order()
        self.client.force_login(self.chief)
        entry_date = timezone.localdate().isoformat()

        materials_response = self.client.post(
            reverse("work-order-materials-received", args=[order.pk])
        )
        diary_response = self.client.post(
            reverse("work-order-diary-create", args=[order.pk]),
            {
                "entry_date": entry_date,
                "description": "Se verificó la instalación.",
                "operators": [self.technician.pk],
            },
        )

        self.assertEqual(materials_response.status_code, 302)
        self.assertEqual(diary_response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.materials_received_by, self.chief)
        self.assertTrue(
            order.diary_entries.filter(description="Se verificó la instalación.").exists()
        )

    def test_operator_cannot_change_status_or_record_materials_or_diary(self):
        order = self.create_order()
        assign_technicians(order=order, actor=self.supervisor, technicians=[self.technician])
        self.client.force_login(self.technician)

        status_response = self.client.post(
            reverse("work-order-status", args=[order.pk]),
            {"status": WorkOrder.Status.IN_PROGRESS},
        )
        materials_response = self.client.post(
            reverse("work-order-materials-received", args=[order.pk])
        )
        diary_response = self.client.post(
            reverse("work-order-diary-create", args=[order.pk]),
            {
                "entry_date": timezone.localdate().isoformat(),
                "description": "Trabajo",
                "operators": [self.technician.pk],
            },
        )

        self.assertEqual(status_response.status_code, 403)
        self.assertEqual(materials_response.status_code, 403)
        self.assertEqual(diary_response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrder.Status.PREPARATION)
        self.assertIsNone(order.materials_received_at)
        self.assertFalse(order.diary_entries.exists())

    def test_assigned_operator_and_technician_can_request_materials(self):
        order = self.create_order()
        assign_technicians(
            order=order,
            actor=self.supervisor,
            technicians=[self.technician, self.technical_staff],
        )
        material = Material.objects.create(name="Cable", unit="m")

        operator_request = create_material_request(
            order=order,
            actor=self.technician,
            material=material,
            quantity=Decimal("3"),
        )
        technical_request = create_material_request(
            order=order,
            actor=self.technical_staff,
            material=material,
            quantity=Decimal("2"),
        )

        self.assertEqual(operator_request.work_order, order)
        self.assertEqual(operator_request.status, WorkOrderMaterialRequest.Status.PENDING)
        self.assertEqual(technical_request.requested_by, self.technical_staff)

    def test_assigned_operator_can_request_material_from_order_detail(self):
        order = self.create_order()
        assign_technicians(order=order, actor=self.supervisor, technicians=[self.technician])
        material = Material.objects.create(name="Guantes", unit="par")
        self.client.force_login(self.technician)

        response = self.client.post(
            reverse("work-order-material-request-create", args=[order.pk]),
            {
                "material": material.pk,
                "requested_quantity": "2",
                "notes": "Para el mantenimiento",
            },
        )

        self.assertEqual(response.status_code, 302)
        material_request = WorkOrderMaterialRequest.objects.get(work_order=order)
        self.assertEqual(material_request.material, material)
        self.assertEqual(material_request.requested_by, self.technician)
        self.assertContains(
            self.client.get(reverse("work-order-detail", args=[order.pk])),
            "Pedidos de materiales",
        )

    def test_panolero_can_create_catalog_material(self):
        self.client.force_login(self.storekeeper)

        response = self.client.post(
            reverse("materials-dashboard"),
            {
                "action": "create_material",
                "name": "Junta",
                "unit": "unidad",
                "minimum_stock": "5",
            },
        )

        self.assertEqual(response.status_code, 302)
        material = Material.objects.get(name="Junta")
        self.assertEqual(material.quantity_in_stock, Decimal("0"))
        self.assertEqual(material.minimum_stock, Decimal("5"))

    def test_panolero_records_receipt_and_partially_delivers_with_stock_history(self):
        order = self.create_order()
        material = Material.objects.create(name="Rodamiento", unit="unidad")
        material_request = create_material_request(
            order=order,
            actor=self.supervisor,
            material=material,
            quantity=Decimal("4"),
        )
        self.client.force_login(self.storekeeper)

        receipt_response = self.client.post(
            reverse("materials-dashboard"),
            {
                "action": "receive_stock",
                "material": material.pk,
                "quantity": "10",
                "notes": "Ingreso de proveedor",
            },
        )
        delivery_response = self.client.post(
            reverse("material-request-deliver", args=[material_request.pk]),
            {"quantity": "2", "notes": "Primera entrega"},
        )

        self.assertEqual(receipt_response.status_code, 302)
        self.assertEqual(delivery_response.status_code, 302)
        material.refresh_from_db()
        material_request.refresh_from_db()
        self.assertEqual(material.quantity_in_stock, Decimal("8.000"))
        self.assertEqual(material_request.delivered_quantity, Decimal("2.000"))
        self.assertEqual(material_request.status, WorkOrderMaterialRequest.Status.PARTIAL)
        self.assertEqual(
            list(
                MaterialStockMovement.objects.order_by("kind").values_list(
                    "kind", "quantity", "work_order_id"
                )
            ),
            [
                (MaterialStockMovement.Kind.DELIVERY, Decimal("2.000"), order.pk),
                (MaterialStockMovement.Kind.RECEIPT, Decimal("10.000"), None),
            ],
        )

    def test_delivery_cannot_exceed_requested_quantity_or_stock(self):
        order = self.create_order()
        material = Material.objects.create(
            name="Grasa",
            unit="kg",
            quantity_in_stock=Decimal("2"),
        )
        material_request = create_material_request(
            order=order,
            actor=self.supervisor,
            material=material,
            quantity=Decimal("3"),
        )

        with self.assertRaises(ValidationError):
            deliver_material_request(
                request=material_request,
                actor=self.storekeeper,
                quantity=Decimal("3"),
            )
        material.refresh_from_db()
        material_request.refresh_from_db()
        self.assertEqual(material.quantity_in_stock, Decimal("2.000"))
        self.assertEqual(material_request.delivered_quantity, Decimal("0.000"))
        self.assertFalse(MaterialStockMovement.objects.exists())

    def test_only_panolero_or_chief_can_manage_stock(self):
        material = Material.objects.create(name="Filtro", unit="unidad")

        with self.assertRaises(PermissionDenied):
            receive_material_stock(
                actor=self.supervisor,
                material=material,
                quantity=Decimal("1"),
            )

        self.client.force_login(self.storekeeper)
        self.assertEqual(self.client.get(reverse("materials-dashboard")).status_code, 200)

    def test_supervisor_can_assign_technicians_directly_and_assignment_is_audited(self):
        order = self.create_order()

        assign_technicians(
            order=order,
            actor=self.supervisor,
            technicians=[self.technician],
        )
        order.refresh_from_db()

        self.assertQuerySetEqual(order.technicians.all(), [self.technician], transform=lambda user: user)
        self.assertEqual(
            order.history.first().action,
            "Asignación de personal técnico actualizada",
        )

    def test_unassigned_supervisor_cannot_change_order(self):
        order = self.create_order()

        with self.assertRaises(PermissionDenied):
            update_work_order_status(
                order=order,
                actor=self.other_supervisor,
                new_status=WorkOrder.Status.IN_PROGRESS,
            )

    def test_change_request_only_applies_after_chief_approval(self):
        order = self.create_order()
        change_request = submit_change_request(
            order=order,
            actor=self.supervisor,
            proposed_values=self.change_values(order),
            reason="Reprogramación solicitada",
        )
        order.refresh_from_db()

        self.assertEqual(order.start_date, date(2026, 10, 1))
        self.assertEqual(change_request.status, WorkOrderChangeRequest.Status.PENDING)

        resolve_change_request(
            request=change_request,
            actor=self.chief,
            approve=True,
            comment="Aprobado",
        )
        order.refresh_from_db()
        change_request.refresh_from_db()

        self.assertEqual(order.start_date, date(2026, 10, 10))
        self.assertEqual(order.estimated_days, 7)
        self.assertEqual(order.equipment, self.other_equipment)
        self.assertEqual(list(order.supervisors.all()), [self.other_supervisor])
        self.assertEqual(change_request.status, WorkOrderChangeRequest.Status.APPROVED)

    def test_stale_change_request_cannot_overwrite_newer_values(self):
        order = self.create_order()
        change_request = submit_change_request(
            order=order,
            actor=self.supervisor,
            proposed_values=self.change_values(order),
            reason="Reprogramación solicitada",
        )
        order.start_date = date(2026, 10, 3)
        order.save(update_fields=["start_date"])

        with self.assertRaises(ValidationError):
            resolve_change_request(
                request=change_request,
                actor=self.chief,
                approve=True,
            )

        order.refresh_from_db()
        self.assertEqual(order.start_date, date(2026, 10, 3))

    def test_supervisor_cannot_open_another_orders_detail(self):
        order = self.create_order()
        self.client.force_login(self.other_supervisor)

        response = self.client.get(reverse("work-order-detail", args=[order.pk]))

        self.assertEqual(response.status_code, 403)

    def test_chief_can_create_work_order_from_form(self):
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("work-order-create"),
            {
                "title": "Cambio de filtro",
                "description": "",
                "equipment": self.equipment.pk,
                "machine_part": self.part.pk,
                "start_date": "2026-10-12",
                "estimated_days": 3,
                "supervisors": [self.supervisor.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        order = WorkOrder.objects.get(title="Cambio de filtro")
        self.assertEqual(list(order.supervisors.all()), [self.supervisor])
        self.assertEqual(order.history.count(), 1)

    def test_supervisor_submits_request_and_chief_approves_in_ui(self):
        order = self.create_order()
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("work-order-change-request", args=[order.pk]),
            {
                "start_date": "2026-10-10",
                "estimated_days": 7,
                "equipment": self.other_equipment.pk,
                "machine_part": self.other_part.pk,
                "supervisors": [self.other_supervisor.pk],
                "reason": "Reprogramación por disponibilidad",
            },
        )

        self.assertEqual(response.status_code, 302)
        change_request = WorkOrderChangeRequest.objects.get(work_order=order)
        self.client.force_login(self.chief)
        listing = self.client.get(reverse("change-request-list"))
        self.assertContains(listing, "Reprogramación por disponibilidad")
        self.assertContains(listing, "Bomba")

        response = self.client.post(
            reverse("change-request-resolve", args=[change_request.pk]),
            {"decision": "approve", "comment": "Aprobado"},
        )

        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        change_request.refresh_from_db()
        self.assertEqual(order.start_date, date(2026, 10, 10))
        self.assertEqual(change_request.status, WorkOrderChangeRequest.Status.APPROVED)

    def test_work_order_can_link_assets_from_the_asset_tree(self):
        motor_type = AssetType.objects.create(name="Motor orden", key="motor_order")
        motor = Asset.objects.create(name="Motor bomba 4", asset_type=motor_type)
        order = self.create_order(equipment=None, machine_part=None, assets=[motor])

        self.assertQuerySetEqual(order.assets.all(), [motor], transform=lambda asset: asset)
        self.client.force_login(self.chief)
        response = self.client.get(reverse("work-order-detail", args=[order.pk]))
        pdf = self.client.get(reverse("work-order-pdf", args=[order.pk]))

        self.assertContains(response, "Motor bomba 4")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        asset_listing = self.client.get(reverse("asset-list"))
        self.assertContains(asset_listing, "Motor bomba 4")

    def test_chief_can_create_work_order_linked_to_asset_tree(self):
        motor_type = AssetType.objects.create(name="Motor formulario", key="motor_form")
        motor = Asset.objects.create(name="Motor selección", asset_type=motor_type)
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("work-order-create"),
            {
                "title": "Revisar motor",
                "description": "Preventivo",
                "assets": [motor.pk],
                "equipment": "",
                "machine_part": "",
                "start_date": "2026-10-12",
                "estimated_days": 3,
                "supervisors": [self.supervisor.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        order = WorkOrder.objects.get(title="Revisar motor")
        self.assertQuerySetEqual(order.assets.all(), [motor], transform=lambda asset: asset)

    def test_change_request_can_update_linked_assets_after_approval(self):
        first_type = AssetType.objects.create(name="Motor A", key="motor_a")
        second_type = AssetType.objects.create(name="Motor B", key="motor_b")
        first_asset = Asset.objects.create(name="Motor actual", asset_type=first_type)
        second_asset = Asset.objects.create(name="Motor propuesto", asset_type=second_type)
        order = self.create_order(equipment=None, machine_part=None, assets=[first_asset])
        values = self.change_values(
            order,
            equipment=None,
            machine_part=None,
            assets=[second_asset],
        )

        request = submit_change_request(
            order=order,
            actor=self.supervisor,
            proposed_values=values,
            reason="Cambio de motor",
        )
        resolve_change_request(request=request, actor=self.chief, approve=True)
        order.refresh_from_db()

        self.assertQuerySetEqual(order.assets.all(), [second_asset], transform=lambda asset: asset)

    def test_chief_can_create_equipment_and_part_from_equipment_page(self):
        self.client.force_login(self.chief)
        new_equipment_response = self.client.post(
            reverse("equipment-list"),
            {
                "action": "create_equipment",
                "equipment-name": "Torno",
                "equipment-description": "Torno principal",
                "equipment-location": "Taller",
            },
        )
        self.assertEqual(new_equipment_response.status_code, 302)
        equipment = Equipment.objects.get(name="Torno")
        part_response = self.client.post(
            reverse("equipment-list"),
            {
                "action": "create_part",
                "part-equipment": equipment.pk,
                "part-name": "Husillo",
            },
        )

        self.assertEqual(part_response.status_code, 302)
        self.assertTrue(MachinePart.objects.filter(equipment=equipment, name="Husillo").exists())

    def test_non_chief_cannot_manage_equipment(self):
        self.client.force_login(self.supervisor)

        response = self.client.get(reverse("equipment-list"))

        self.assertEqual(response.status_code, 403)

    def test_chief_can_create_accounts_for_each_role(self):
        self.client.force_login(self.chief)
        for username, role in (
            ("new-chief", CHIEF),
            ("new-supervisor", SUPERVISOR),
            ("new-operator", OPERATOR),
            ("new-technician", TECHNICIAN),
            ("new-storekeeper", STOREKEEPER),
        ):
            with self.subTest(role=role):
                response = self.client.post(
                    reverse("staff-create"),
                    {
                        "username": username,
                        "first_name": "Nuevo",
                        "last_name": role,
                        "email": f"{username}@example.test",
                        "role": role,
                        "password1": "Strong-Test-Password-873!",
                        "password2": "Strong-Test-Password-873!",
                    },
                )
                self.assertEqual(response.status_code, 302)
                user = User.objects.get(username=username)
                self.assertTrue(user.groups.filter(name=role).exists())
                self.assertTrue(user.check_password("Strong-Test-Password-873!"))

    def test_operator_can_only_view_assigned_order_and_download_pdf(self):
        order = self.create_order()
        assign_technicians(order=order, actor=self.supervisor, technicians=[self.technician])
        self.client.force_login(self.technician)

        detail = self.client.get(reverse("work-order-detail", args=[order.pk]))
        pdf = self.client.get(reverse("work-order-pdf", args=[order.pk]))
        status_change = self.client.post(
            reverse("work-order-status", args=[order.pk]),
            {"status": WorkOrder.Status.IN_PROGRESS},
        )
        equipment_management = self.client.get(reverse("equipment-list"))
        user_management = self.client.get(reverse("staff-list"))

        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Actualizar estado")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(status_change.status_code, 403)
        self.assertEqual(equipment_management.status_code, 403)
        self.assertEqual(user_management.status_code, 403)
        self.assertEqual(
            self.client.get(reverse("asset-list")).status_code,
            403,
        )

    def test_operator_cannot_view_or_download_unassigned_order(self):
        order = self.create_order()
        self.client.force_login(self.technician)

        detail = self.client.get(reverse("work-order-detail", args=[order.pk]))
        pdf = self.client.get(reverse("work-order-pdf", args=[order.pk]))

        self.assertEqual(detail.status_code, 403)
        self.assertEqual(pdf.status_code, 403)

    def test_asset_hierarchy_has_custom_specifications_and_floor(self):
        floor_type = AssetType.objects.create(
            name="Piso",
            key="piso_test",
            is_location=True,
        )
        motor_type = AssetType.objects.create(
            name="Motor de prueba",
            key="motor_test",
            specification_fields=[
                {"key": "power", "label": "Potencia", "unit": "kW"}
            ],
        )
        floor = Asset.objects.create(name="Piso 2", asset_type=floor_type)
        panel = Asset.objects.create(
            name="Tablero bombas",
            asset_type=self._asset_type("tablero_test", "Tablero de prueba"),
            parent=floor,
        )
        self.client.force_login(self.chief)
        self.assertContains(self.client.get(reverse("asset-create")), "Potencia")
        response = self.client.post(
            reverse("asset-create"),
            {
                "name": "Motor X",
                "asset_type": motor_type.pk,
                "parent": panel.pk,
                "location": floor.pk,
                "brand": "Marca",
                "model": "MX-1",
                "serial_number": "SN-1",
                "notes": "",
                "spec__motor_test__power": "15",
            },
        )

        self.assertEqual(response.status_code, 302)
        motor = Asset.objects.get(name="Motor X")
        self.assertEqual(motor.specifications, {"power": "15"})
        self.assertEqual(motor.floor, floor)
        detail = self.client.get(reverse("asset-detail", args=[motor.pk]))
        self.assertContains(detail, "Potencia")
        self.assertContains(detail, "15")

    def _asset_type(self, key, name):
        return AssetType.objects.create(name=name, key=key)

    def test_assets_can_be_connected_across_branches(self):
        asset_type = self._asset_type("connection_test", "Activo de conexión")
        panel = Asset.objects.create(name="Tablero norte", asset_type=asset_type)
        valve = Asset.objects.create(name="Válvula línea 1", asset_type=asset_type)
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("asset-connection-create"),
            {
                "source": panel.pk,
                "kind": AssetConnection.Kind.POWERED_BY,
                "target": valve.pk,
                "notes": "Alimentación de prueba",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AssetConnection.objects.filter(
                source=panel,
                target=valve,
                kind=AssetConnection.Kind.POWERED_BY,
            ).exists()
        )

    def test_floor_can_be_added_under_plant_without_duplicate_location(self):
        plant_type = AssetType.objects.create(
            name="Planta de prueba",
            key="plant_test",
            is_location=True,
        )
        floor_type = AssetType.objects.create(
            name="Piso de prueba",
            key="floor_test",
            is_location=True,
        )
        plant = Asset.objects.create(name="Planta sur", asset_type=plant_type)
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("asset-create"),
            {
                "name": "Piso 1",
                "asset_type": floor_type.pk,
                "parent": plant.pk,
                "location": "",
                "brand": "",
                "model": "",
                "serial_number": "",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        floor = Asset.objects.get(name="Piso 1")
        self.assertEqual(floor.parent, plant)
        self.assertIsNone(floor.location)

    def test_preventive_record_advances_next_due_date_from_completion(self):
        asset_type = self._asset_type("preventive_test", "Bomba preventiva")
        asset = Asset.objects.create(name="Bomba 1", asset_type=asset_type)
        plan = PreventiveMaintenancePlan.objects.create(
            asset=asset,
            name="Inspección de bomba",
            interval_days=30,
            next_due_date=timezone.localdate(),
            reminder_days_before=5,
            created_by=self.chief,
        )
        plan.supervisors.add(self.supervisor)
        self.client.force_login(self.supervisor)

        response = self.client.post(
            reverse("preventive-maintenance-record", args=[plan.pk]),
            {"performed_on": timezone.localdate().isoformat(), "notes": "Revisada"},
        )

        self.assertEqual(response.status_code, 302)
        plan.refresh_from_db()
        self.assertEqual(plan.next_due_date, timezone.localdate() + timedelta(days=30))
        self.assertEqual(PreventiveMaintenanceRecord.objects.filter(plan=plan).count(), 1)
        self.assertContains(self.client.get(reverse("preventive-plan-list")), "Inspección de bomba")

    def test_chief_can_create_preventive_plan_from_asset(self):
        asset_type = self._asset_type("plan_form", "Activo plan")
        asset = Asset.objects.create(name="Tablero 2", asset_type=asset_type)
        self.client.force_login(self.chief)

        response = self.client.post(
            reverse("preventive-plan-create"),
            {
                "asset": asset.pk,
                "name": "Inspección anual",
                "instructions": "Revisar protecciones",
                "interval_days": 365,
                "next_due_date": "2027-10-31",
                "reminder_days_before": 14,
                "supervisors": [self.supervisor.pk],
            },
        )

        self.assertEqual(response.status_code, 302)
        plan = PreventiveMaintenancePlan.objects.get(name="Inspección anual")
        self.assertQuerySetEqual(
            plan.supervisors.all(),
            [self.supervisor],
            transform=lambda user: user,
        )

    def test_preventive_reminder_command_sends_once_and_tracks_recipients(self):
        asset_type = self._asset_type("reminder_test", "Activo preventivo")
        asset = Asset.objects.create(name="Equipo recordatorio", asset_type=asset_type)
        plan = PreventiveMaintenancePlan.objects.create(
            asset=asset,
            name="Servicio trimestral",
            interval_days=90,
            next_due_date=timezone.localdate() + timedelta(days=2),
            reminder_days_before=7,
            created_by=self.chief,
        )
        plan.supervisors.add(self.supervisor)

        call_command("send_preventive_reminders")
        call_command("send_preventive_reminders")

        self.assertEqual(len(mail.outbox), 1)
        reminder = PreventiveMaintenanceReminder.objects.get(plan=plan)
        self.assertEqual(reminder.due_date, plan.next_due_date)
        self.assertIn(self.chief.email, reminder.recipients)
        self.assertIn(self.supervisor.email, reminder.recipients)

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.test import override_settings
from unittest.mock import patch

from .models import Equipment, MachinePart, WorkOrder, WorkOrderChangeRequest
from .roles import CHIEF, SUPERVISOR, TECHNICIAN
from .services import (
    assign_technicians,
    create_work_order,
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
        cls.technician_group = Group.objects.create(name=TECHNICIAN)
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
        cls.technician.groups.add(cls.technician_group)
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

    def test_supervisor_can_assign_technicians_directly_and_assignment_is_audited(self):
        order = self.create_order()

        assign_technicians(
            order=order,
            actor=self.supervisor,
            technicians=[self.technician],
        )
        order.refresh_from_db()

        self.assertQuerySetEqual(order.technicians.all(), [self.technician], transform=lambda user: user)
        self.assertEqual(order.history.first().action, "Asignación de técnicos actualizada")

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

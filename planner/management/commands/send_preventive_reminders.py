import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.mail import send_mail
from django.db import transaction
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone

from planner.models import (
    PreventiveMaintenancePlan,
    PreventiveMaintenanceReminder,
)
from planner.roles import CHIEF

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Envía una vez el recordatorio de cada próximo mantenimiento preventivo."

    def handle(self, *args, **options):
        today = timezone.localdate()
        plans = (
            PreventiveMaintenancePlan.objects.filter(is_active=True)
            .select_related("asset", "asset__asset_type")
            .prefetch_related("supervisors")
            .order_by("next_due_date")
        )
        sent_count = 0
        for plan in plans.iterator(chunk_size=100):
            remind_on = plan.next_due_date - timedelta(days=plan.reminder_days_before)
            if today < remind_on:
                continue
            with transaction.atomic():
                locked_plan = (
                    PreventiveMaintenancePlan.objects.select_for_update()
                    .select_related("asset")
                    .get(pk=plan.pk)
                )
                if PreventiveMaintenanceReminder.objects.filter(
                    plan=locked_plan,
                    due_date=locked_plan.next_due_date,
                ).exists():
                    continue
                chief_group_users = get_user_model().objects.filter(
                    groups__name=CHIEF,
                    is_active=True,
                )
                superusers = get_user_model().objects.filter(
                    is_superuser=True,
                    is_active=True,
                )
                supervisors = locked_plan.supervisors.filter(is_active=True)
                recipients = sorted(
                    {
                        user.email
                        for user in (*chief_group_users, *superusers, *supervisors)
                        if user.email
                    }
                )
                if not recipients:
                    self.stderr.write(
                        f"Sin destinatarios con email para el plan #{locked_plan.pk}; "
                        "no se marcó como enviado."
                    )
                    continue
                due_date = locked_plan.next_due_date
                subject = f"Mantenimiento preventivo próximo: {locked_plan.name}"
                body = (
                    f"El mantenimiento preventivo '{locked_plan.name}' para el activo "
                    f"'{locked_plan.asset.name}' ({locked_plan.asset.asset_type.name}) "
                    f"está programado para el {due_date:%d/%m/%Y}.\n\n"
                    f"Instrucciones: {locked_plan.instructions or 'Sin instrucciones adicionales.'}"
                )
                send_mail(
                    subject,
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    recipients,
                    fail_silently=False,
                )
                PreventiveMaintenanceReminder.objects.create(
                    plan=locked_plan,
                    due_date=due_date,
                    recipients=recipients,
                )
                sent_count += 1
                self.stdout.write(f"Enviado: plan #{locked_plan.pk} ({due_date:%d/%m/%Y})")
        self.stdout.write(self.style.SUCCESS(f"Recordatorios enviados: {sent_count}."))

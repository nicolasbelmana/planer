from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from planner.roles import ROLE_NAMES


class Command(BaseCommand):
    help = "Crea los grupos de roles de Maintenance Planner si todavía no existen."

    def handle(self, *args, **options):
        for name in ROLE_NAMES:
            _, created = Group.objects.get_or_create(name=name)
            message = "Creado" if created else "Ya existe"
            self.stdout.write(f"{message}: {name}")

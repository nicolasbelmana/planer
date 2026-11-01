CHIEF = "Jefe"
SUPERVISOR = "Supervisor"
TECHNICIAN = "Tecnico"
ROLE_NAMES = (CHIEF, SUPERVISOR, TECHNICIAN)


def has_role(user, role):
    if not user.is_authenticated:
        return False
    if user.is_superuser and role == CHIEF:
        return True
    return user.groups.filter(name=role).exists()

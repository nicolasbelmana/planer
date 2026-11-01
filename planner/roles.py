CHIEF = "Jefe"
SUPERVISOR = "Supervisor"
OPERATOR = "Operario"
TECHNICIAN = "Técnico"
STOREKEEPER = "Pañolero"
WORK_ORDER_STAFF_ROLES = (OPERATOR, TECHNICIAN)
ROLE_NAMES = (CHIEF, SUPERVISOR, OPERATOR, TECHNICIAN, STOREKEEPER)


def has_role(user, role):
    if not user.is_authenticated:
        return False
    if user.is_superuser and role == CHIEF:
        return True
    return user.groups.filter(name=role).exists()

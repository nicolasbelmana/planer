# Maintenance Planner

Aplicación web inicial para administrar órdenes de mantenimiento con Django. Usa SQLite en desarrollo local y MySQL cuando se configuran las variables `MYSQL_*`.

## Requisitos

- Python 3.10 o posterior
- Django (ver `requirements.txt`)
- MySQL y `mysqlclient` para usar MySQL

## Preparación local

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_roles
python manage.py createsuperuser
python manage.py runserver
```

En `/admin/`, asigna los grupos **Jefe**, **Supervisor** y **Tecnico** a las cuentas correspondientes. El superusuario también tiene permisos de Jefe. Usa el administrador para dar de alta equipos y partes.

## Base de datos MySQL

Configura estas variables de entorno antes de ejecutar migraciones o iniciar el servidor:

```powershell
$env:MYSQL_DATABASE = "maintenance_planner"
$env:MYSQL_USER = "usuario"
$env:MYSQL_PASSWORD = "contraseña"
$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "3306"
```

Si `MYSQL_DATABASE` no está definida, se usa SQLite para facilitar el desarrollo local y las pruebas.

## Configuración de producción

Define `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=false` y `DJANGO_ALLOWED_HOSTS`. No uses la clave de desarrollo en producción. El backend de email imprime mensajes en consola por defecto; para SMTP, define `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` y `DEFAULT_FROM_EMAIL`.

## Reglas implementadas

- El jefe crea órdenes y asigna supervisores.
- Supervisores asignados actualizan estado y administran la asignación de técnicos directamente.
- Los cambios de fechas, duración, equipo, parte o supervisores requieren aprobación del jefe. Se conserva la solicitud y el cambio pendiente no altera la orden. Los supervisores asignados pueden asignar y cambiar técnicos directamente.
- La fecha de inicio cuenta como día 1: la fecha límite es `fecha_inicio + días_estimados - 1`.
- El vencimiento es un indicador calculado; no reemplaza el estado operativo.
- Flujo de estado: En preparación → Esperando materiales → En curso → Finalizado; también se permite En preparación → En curso → Finalizado.
- Se mantiene historial de creación, cambios de estado, asignaciones y solicitudes/resoluciones.

## Validación

```powershell
python manage.py check
python manage.py test
```

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
python manage.py setup_asset_types
python manage.py createsuperuser
python manage.py runserver
```

Ingresa con el superusuario y usa la sección **Personal** para crear cuentas Jefe, Supervisor, Operario, Técnico y Pañolero; desde **Equipos** registra equipos y partes. El jefe crea órdenes y asigna supervisores; el supervisor asigna operarios y técnicos. El personal técnico solo ve órdenes asignadas y puede descargar su PDF. El superusuario también tiene permisos de Jefe.

## Materiales y pañol

En **Materiales**, el Jefe o Pañolero puede crear materiales, definir un mínimo de stock, registrar ingresos y entregar pedidos. Los ingresos y entregas quedan en un historial de movimientos. Operarios, Técnicos y Supervisores pueden consultar el catálogo y sus pedidos.

Desde el detalle de una orden, el Jefe, el Supervisor asignado y el personal técnico asignado pueden solicitar materiales. Cada pedido queda relacionado con esa orden y se puede entregar parcial o totalmente. El stock se descuenta al registrar la entrega; no se reserva al crear el pedido. No hace falta aprobación del Jefe para que el Pañolero lo procese.

## Árbol de activos y mantenimiento preventivo

Ejecuta `python manage.py setup_asset_types` para cargar los tipos iniciales (planta, piso, sistema, tablero, bomba, motor, cañería, válvula, parte y componente) y sus campos técnicos. En **Activos**, el jefe puede construir una jerarquía, registrar ubicación, marca, modelo, número de serie y especificaciones, así como vincular activos de ramas distintas. Las órdenes nuevas pueden asociarse a uno o más activos del árbol; los campos de equipo simple se conservan para compatibilidad con datos anteriores. El superusuario puede ajustar tipos y campos técnicos en `/admin/`.

En **Preventivos**, el jefe programa una fecha inicial, intervalo en días corridos, anticipación del aviso y supervisores responsables. Los responsables registran cuándo se hizo el mantenimiento; la siguiente fecha se calcula desde ese día más el intervalo. El recordatorio por email incluye jefes y supervisores asignados y se registra para no duplicarlo. Configura SMTP para enviar email real.

Programa la comprobación diaria en el servidor (Linux, con cron), reemplazando la ruta por la instalación real:

```cron
0 8 * * * cd /ruta/planer && /ruta/planer/.venv/bin/python manage.py send_preventive_reminders
```

En Windows Server, crea una tarea diaria en el Programador de tareas que ejecute `python.exe` con argumento `manage.py send_preventive_reminders` y como directorio de inicio la carpeta del proyecto.

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
- El jefe puede actualizar el estado, confirmar la recepción de materiales y registrar trabajos en cualquier orden. Los supervisores asignados pueden realizar esas mismas actualizaciones en sus órdenes.
- La recepción de materiales se registra por separado del estado, incluyendo quién y cuándo la confirmó.
- El diario de trabajo registra fecha, descripción, quién lo ingresó y los operarios o técnicos participantes; se puede incluir a cualquier cuenta técnica activa. El diario aparece en el PDF.
- Los supervisores asignados administran la asignación de operarios y técnicos directamente.
- Los roles Técnico y Operario son distintos; ambos pueden asignarse a órdenes y solicitar materiales cuando están asignados.
- El Pañolero administra el catálogo y el stock, y procesa pedidos sin aprobación previa. El stock solo se descuenta al registrar una entrega.
- Los cambios de fechas, duración, equipo, parte o supervisores requieren aprobación del jefe. Se conserva la solicitud y el cambio pendiente no altera la orden. Los supervisores asignados pueden asignar y cambiar operarios directamente.
- La fecha de inicio cuenta como día 1: la fecha límite es `fecha_inicio + días_estimados - 1`.
- El vencimiento es un indicador calculado; no reemplaza el estado operativo.
- Flujo de estado: En preparación → Esperando materiales → En curso → Finalizado; también se permite En preparación → En curso → Finalizado.
- Se mantiene historial de creación, cambios de estado, asignaciones y solicitudes/resoluciones.
- Los Operarios y Técnicos no pueden modificar órdenes ni administrar personal; solo consultar órdenes asignadas, descargar el PDF y solicitar materiales para sus órdenes.

## Validación

```powershell
python manage.py check
python manage.py test
```

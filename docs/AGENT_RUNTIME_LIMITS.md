# Evitar saturación del servidor al ejecutar SEO

Esta actualización parte del PR #3 ya fusionado. Está en `codex/agent-runtime-guards` y añade límites de ejecución y de recursos. No se ha conectado al VPS: las causas descritas son defectos reproducidos o identificados en el código, no una lectura de sus registros de producción.

## Causas corregidas

- El ejecutor predeterminado abría hilos en la web. Ahora usa RQ. El modo `thread` requiere configuración explícita para desarrollo y también respeta la reserva global de plazas.
- El optimizador podía evaluar diez veces las mismas páginas cuando la IA fallaba o no devolvía una mejora. Cada lote intenta una vez cada página y conserva los casos pendientes. El contenido ajeno descubierto por el inventario no se reescribe.
- Una reserva que fallaba al enviarse a Redis no incrementaba el contador de intentos. Se contabiliza desde la reserva; el director espera entre intentos y se detiene después del tercero. También se detiene tras tres errores consecutivos de su propia coordinación.
- Las reservas en cola podían quedar pendientes indefinidamente. Caducan, y un trabajador tardío no puede ejecutar o sobrescribir un trabajo que ya terminó. Una caída o muerte del trabajador queda registrada con reintento automático bloqueado.
- Detener el lote cancelaba los agentes uno a uno mientras el director todavía podía crear más. La cancelación ahora se registra de forma conjunta; las nuevas reservas comprueban que su director sigue activo. RQ recibe además una orden para detener el trabajo en ejecución.
- Los tiempos de espera del lector web se ignoraban. Ahora se aplican durante redirecciones y descarga, con comprobaciones de cancelación. El inventario conserva sus URLs y el avance cada cinco páginas.
- Lighthouse podía dejar procesos de Chrome tras un timeout. Se limita a un navegador por instalación y se terminan los descendientes conocidos al finalizar o interrumpirse el proceso. Los servicios usan `OOMPolicy=kill` y `KillMode=control-group` para limpiar su grupo cuando se agota memoria o se detienen.
- Apify convertía todo el dataset en una lista antes de recortarla. Ahora se consume el iterador hasta el límite solicitado. GSC detecta páginas repetidas y tiene un máximo de paginación por ejecución.

## Valores iniciales

| Control | Valor inicial | Variable |
|---|---:|---|
| Agentes SEO simultáneos, entre todos los clientes | 1 | `DENZO_MAX_RUNNING_AGENTS` |
| Directores simultáneos, en una cola independiente | 1 | `DENZO_MAX_RUNNING_DIRECTORS` |
| Auditorías públicas simultáneas | 1 | `DENZO_MAX_RUNNING_AUDITS` |
| Trabajos pendientes de agentes | 24 | `DENZO_MAX_QUEUED_JOBS` |
| Espera máxima en la cola | 30 minutos | `DENZO_QUEUE_TTL=1800` |
| Duración máxima de un agente | 20 minutos + 30 segundos de margen de terminación RQ | `DENZO_AGENT_TIMEOUT=1200` |
| Duración máxima del director | 2 horas + 30 segundos de margen RQ | `DENZO_DIRECTOR_TIMEOUT=7200` |
| Intentos de petición a Claude por trabajo, incluidos reintentos | 60 | `DENZO_MAX_API_CALLS_PER_JOB` |
| Peticiones Claude simultáneas dentro de un proceso | 1 | `DENZO_API_CONCURRENCY` |
| Páginas de generación/GEO/visual por lote | Hasta 10 | `DENZO_PAGE_BATCH_SIZE` |
| Páginas del optimizador de contenido | Hasta 10, una evaluación por página y lote | También limitado por `DENZO_PAGE_BATCH_SIZE` |
| Páginas del inventario por lote | 50 | `DENZO_INVENTORY_PAGE_BATCH` |
| Paginación de GSC | 20 lotes de hasta 5.000 filas | `DENZO_GSC_MAX_BATCHES` |
| Procesos web Gunicorn | 2 | `DENZO_WEB_WORKERS` |

Las plazas de ejecución se reservan en la misma base SQLite. Añadir trabajadores RQ no elimina estos límites. Las otras APIs siguen sujetas a sus límites de petición y a la duración máxima del trabajo; el contador de 60 es específico de las llamadas compartidas a Claude.

La fase de investigación del director también es secuencial. Si no hay ningún agente ejecutándose ni puede arrancar trabajo durante cinco minutos, el director se detiene para revisar las colas y los requisitos. Una tarea que está ejecutándose conserva su propio plazo de 20 minutos. La programación se desactiva tras tres fallos consecutivos de despacho o al encontrar un fallo que requiere intervención; primero corrige su causa y después vuelve a activarla.

Un timeout o una cancelación no revierten una petición externa que el proveedor ya recibió. Se conservan las reservas de publicación para verificar si WordPress/GitHub la completó antes de volver a publicar.

## Aplicar en el VPS

Los límites del sistema operativo requieren actualizar las unidades de servicio además del código. El archivo nuevo `denzo-background.slice` agrupa trabajadores, director, programador y todas las instancias del auditor. El servicio web queda fuera.

1. Sigue la preparación y copia de seguridad de [la guía de actualización](DEPLOY_PLATFORM_RELIABILITY.md). Deja finalizar los trabajos anteriores y detén los servicios antes de actualizar. Si hay una ejecución bloqueada, usa detener y revisa si recibió alguna publicación el proveedor antes de repetirla.
2. Actualiza a esta rama o al `main` que la incluya e instala `requirements.txt` en el mismo entorno `.venv` de todos los servicios. Se añade `psutil` para controlar descendientes de procesos.
3. Ejecuta la migración antes de reanudar trabajadores:

```bash
cd /root/denzo-seo
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(); from denzo.db import init_db; init_db()'
cp services/denzo-background.slice /etc/systemd/system/
cp services/denzo-worker.service /etc/systemd/system/
cp services/denzo-director.service /etc/systemd/system/
cp services/denzo-scheduler.service /etc/systemd/system/
cp services/denzo-auditor@.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart denzo-web denzo-worker denzo-director denzo-scheduler
```

Adapta `/root/denzo-seo` y los nombres si tu instalación usa otra ruta o una unidad propia. Reinicia también las instancias del auditor que ya utilizabas. Si mantienes unidades personalizadas, incorpora `Slice`, `OOMPolicy`, `KillMode`, `TimeoutStopSec`, los límites de reinicio y `--worker-class denzo.worker.BoundedWorker` de las unidades incluidas. No dejes una unidad antigua ejecutando agentes a la vez.

En `.env`, confirma `DENZO_EXECUTOR=rq`, el mismo `REDIS_URL` y el mismo `DENZO_DB_PATH` para web y trabajadores. Los límites de la tabla tienen esos valores por defecto; puedes declararlos explícitamente para mantener una configuración reproducible.

## Presupuesto de CPU y memoria

El grupo de servicios de fondo tiene `CPUQuota=75%` (tres cuartos de un núcleo), `MemoryHigh=35%`, `MemoryMax=45%`, hasta 256 MiB de swap y 256 tareas. Los porcentajes de memoria se refieren a la RAM física del servidor. Son valores iniciales conservadores: ajústalos al VPS y a los otros proyectos que aloje tras observar un lote. [Referencia de systemd](https://www.freedesktop.org/software/systemd/man/systemd.resource-control.html).

Comprueba que el servidor aplica los controles:

```bash
systemctl show denzo-background.slice -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p MemoryCurrent -p TasksCurrent
systemctl show denzo-worker denzo-director denzo-scheduler -p Slice -p OOMPolicy
systemd-cgtop
journalctl -u denzo-worker -u denzo-director -u denzo-scheduler -n 100 --no-pager
```

Si el límite de memoria termina un trabajo, no lo subas a ciegas ni reinicies la misma tarea repetidamente: revisa el tamaño de la web/imagen, el agente y la RAM disponible. Tres reinicios de un servicio en cinco minutos agotan el límite de arranque; una vez corregida la causa se puede usar `systemctl reset-failed` para esa unidad concreta.

## Verificación

Las pruebas cubren concurrencia entre clientes, cola saturada/caída, trabajos huérfanos, cancelación de lotes, límites de duración y API, errores de coordinación, páginas repetidas, continuación del inventario, paginación GSC y limpieza de procesos hijos reales.

GitHub Actions añade un Redis aislado y ejecuta un trabajador RQ real para tres casos: finalización normal, timeout y proceso terminado abruptamente. Para repetir esas pruebas fuera de CI, configura `DENZO_TEST_REDIS_URL` con una instancia dedicada a pruebas. Sin esa variable se omiten únicamente esas tres pruebas.

En el VPS, ejecuta primero un lote y comprueba que la web sigue respondiendo, que el trabajo termina dentro de sus límites y que «detener» deja de consumir recursos. Esta revisión no incluye una medición de carga contra tu servidor ni sus credenciales de producción. [Ciclo de fallos y reintentos de RQ](https://python-rq.org/docs/exceptions/).

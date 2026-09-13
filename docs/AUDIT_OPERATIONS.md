# DENZO: auditorías fiables para Droppin

## Qué cambia

El endpoint POST `/auditor/analyze` reserva un trabajo en SQLite y devuelve su identificador. La petición web no ejecuta el análisis ni depende de un hilo en memoria. El worker `python -m denzo.auditor.worker` toma la reserva y ejecuta un proceso independiente por auditoría, con un límite de 600 segundos. La reserva dura 660 segundos y usa un token que impide guardar el resultado de un proceso obsoleto. Un crash permite hasta tres intentos; un error de análisis queda fallido para revisión y una nueva solicitud explícita.

La cola, cuotas por hora, progreso y heartbeats comparten `data/denzo.db` con la aplicación existente. Se crean automáticamente las tablas `audit_jobs`, `audit_rate_limits` y `audit_worker_heartbeats`, sin borrar informes. Las antiguas solicitudes pendientes que dependían de hilos se marcan fallidas: hay que solicitarlas de nuevo. El GET `/auditor/go` no crea trabajo. Un reinicio del servidor web no pierde las reservas.

El `AUDIT_SERVICE_TOKEN` de Droppin autentica las solicitudes. El cliente debe mandar una `Idempotency-Key` estable de 16–80 caracteres; queda ligada permanentemente a la URL y repetirla no consume otra cuota ni crea otro análisis. El modo público está desactivado por defecto. Una sesión autenticada puede analizar desde la interfaz; habilitar el modo público requiere un ajuste deliberado y conserva una cuota independiente.

## Puntuación `droppin-audit-v2`

| Módulo | Peso general | Peso negocio local |
| --- | ---: | ---: |
| Técnico | 30 | 25 |
| Estructura y señales GEO | 22 | 17 |
| Rendimiento móvil | 15 | 15 |
| Sitemap | 8 | 8 |
| Robots | 7 | 7 |
| Imágenes | 8 | 8 |
| Contenido | 10 | 10 |
| Señales de negocio local | 0 | 10 |

Los módulos de palabras clave, llms.txt, citas de IA, investigación e indexación tienen peso cero. Cada análisis copia sus pesos: identificar un negocio local no modifica los siguientes análisis. La nota es una media ponderada redondeada de forma idéntica a JavaScript; cobertura es la suma de pesos con medición válida. Solo cobertura 100 permite `commercial_ready=true`.

PageSpeed toma el score Lighthouse de 0–1 y lo convierte a 0–100, conserva el cero real, usa codificación correcta de la URL y separa medición de laboratorio de datos CrUX. No inventa tiempos ni Core Web Vitals cuando la API falla. El CLS de campo se convierte desde el percentil de la API; no se confunde con segundos o un score. Una medición ausente es `null`, no una puntuación de cero que haga parecer mala la web. No se penaliza dos veces el rendimiento por las mismas métricas.

`report_json` conserva fecha UTC, versión, URL final, pesos, puntuaciones y estado por módulo, cobertura y alcance. El cliente Droppin valida ese contrato y su aritmética antes de permitir una postal. Las respuestas incompletas no muestran una nota comercial ni esconden evidencia detrás de una promesa de mejora. Los informes antiguos siguen siendo legibles, pero no cumplen el nuevo contrato de impresión; deben repetirse.

La nota es un diagnóstico automatizado de la página de entrada y señales detectables. No representa posición en Google, pérdida de clientes, tráfico previsto ni una auditoría exhaustiva de todo el dominio. Restringir un bot de IA en robots.txt no demuestra un perjuicio en Google. No presentar el reporte como si tuviera esa certeza.

## Lectura de webs y límites

El auditor usa `safe_fetch` en la página principal, robots, sitemap y llms.txt. Comprueba todas las IP resueltas y cada redirección, fija la IP de conexión y conserva SNI/validación TLS del hostname. Solo admite HTTP/HTTPS públicos, sin credenciales ni puertos arbitrarios. Limita redirecciones, bytes y tiempo; no recurre a un proxy de evasión. Los errores de red, límites de acceso y páginas que no ofrecen contenido analizable quedan incompletos o fallidos.

`AUDIT_ENABLE_AI_ENRICHMENT=false` es el valor predeterminado: evita peticiones adicionales a Claude y servicios de investigación durante los lotes. Si se habilita, esos resultados son contexto complementario; no cambian los pesos de la nota. Esto no convierte un sitio dependiente de JavaScript o protegido contra bots en una medición fiable: debe quedar pendiente de revisión si no se puede medir.

## Instalación en el servidor existente

El servicio incluido sigue las rutas existentes de este repositorio: `/root/denzo-seo` y su `.env`. Si el servidor usa otra ruta/usuario, adaptar la unidad antes de instalarla. El worker y el servidor web necesitan leer y escribir la misma base SQLite y conservar `data/` entre versiones. La aplicación debe haber inicializado su esquema existente (`site_audits`) antes del primer worker.

```bash
cd /root/denzo-seo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests/test_audit_reliability.py tests/test_droppin_audit_json.py -q
cp services/denzo-auditor@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now denzo-auditor@1
```

Recargar también el servicio web de DENZO con la versión revisada y el mismo entorno. No sustituir el worker RQ existente: pertenece a otras funciones. El worker de auditorías es independiente. Comenzar con una instancia y medir duración y memoria antes de habilitar `denzo-auditor@2`; cada una puede ejecutar varios módulos simultáneamente. Una cola de 1.000 elementos no significa 1.000 procesos concurrentes.

| Variable | Valor/uso |
| --- | --- |
| `AUDIT_SERVICE_TOKEN` | Secreto aleatorio de al menos 32 caracteres, idéntico al de Droppin |
| `PAGESPEED_API_KEY` | Clave de Google con PageSpeed Insights habilitada; obligatoria para arrancar el worker |
| `AUDIT_PUBLIC_ENABLED` | `false` por defecto |
| `AUDIT_SERVICE_LIMIT_PER_HOUR` | 1.200 solicitudes nuevas/hora por defecto para el servicio |
| `AUDIT_RATE_LIMIT_PER_HOUR` | 10 solicitudes nuevas/hora por origen público por defecto |
| `AUDIT_ENABLE_AI_ENRICHMENT` | `false` por defecto; habilitar solo si se necesitan resultados complementarios |

Mantener estos valores fuera de Git y disponer de una copia de seguridad de SQLite. La nueva cola no necesita Redis ni otra base de datos.

## Comprobación operativa

`GET /auditor/health` requiere `Authorization: Bearer <AUDIT_SERVICE_TOKEN>`. Devuelve versión, número de pendientes, presencia de la clave PageSpeed y heartbeat activo. Responde 503 si no hay worker reciente o falta la configuración PageSpeed. Esta lectura no consume un análisis ni confirma que la clave de Google tenga cuota disponible.

Droppin incluye `npm run acquisition:readiness -- --providers`, que comprueba este endpoint sin imprimir secretos. Antes de habilitar un lote real, procesar una web controlada y revisar que la medición PageSpeed real, el JSON y el informe coincidan. Las pruebas automatizadas usan bases temporales y respuestas sintéticas; no han comprobado el servidor ni las cuentas de producción.

La suite de auditorías tiene 27 pruebas: escala Lighthouse y cero real, CLS, ausencia de datos, pesos independientes, cuota persistente, claves repetidas, recuperación y fencing, bloqueo de destinos privados y redirecciones, informe incompleto sin nota, autenticación y salud. Ejecutarla con el comando anterior. No requiere claves ni hace llamadas a proveedores.

Fuentes oficiales: [PageSpeed Insights API](https://developers.google.com/speed/docs/insights/v5/get-started), [Lighthouse performance scoring](https://developer.chrome.com/docs/lighthouse/performance/performance-scoring).

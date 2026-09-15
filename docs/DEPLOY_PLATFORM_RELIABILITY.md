# Actualizar DENZO SEO: fiabilidad y revisión editorial

Esta versión se entrega en la rama `codex/platform-reliability`, mediante el PR #3. La actualización del servidor corresponde al operador. No se ha probado contra las credenciales ni las webs de producción.

## Qué cambia

- Todas las rutas de clientes, exportaciones y menús respetan la propiedad del cliente. La configuración global requiere administrador. Los clientes nuevos tienen propietario desde su creación.
- La aprobación identifica una versión concreta del texto, título, metadatos, slug y schema. Cualquier cambio invalida aprobación y puntuación. La regeneración conserva el texto anterior hasta que exista un sustituto; los cambios de texto guardan historial.
- Los optimizadores trabajan en secuencia, con evaluación de calidad al final. Las respuestas de IA incompletas no reciben una puntuación ficticia. Los hechos verificables se introducen con su fuente en **Brand Voice → Audience and verified facts**; la verificación indicada es la realizada por el cliente, no una certificación automática.
- Los agentes tienen reservas persistentes, exclusión entre escritores del mismo cliente, cancelación cooperativa y detección de trabajadores interrumpidos. Los límites de clientes, páginas generadas y keywords se aplican en la base de datos y los permisos de plan en el ejecutor.
- Una publicación pasa de `ready` a `publishing` tras enviarse. Solo pasa a `published` con `deployment_status=verified` al encontrar en la URL pública el marcador de esa revisión. Una confirmación de GitHub no acredita que el despliegue haya terminado. Las publicaciones históricas conservan su estado; `unknown` no significa que se hayan verificado con este mecanismo.
- Los sitemaps gestionados, URLs canónicas y enlaces internos comparten rutas. Se protegen archivos y páginas ajenos al registro de DENZO.
- Las imágenes conservan sus archivos: se leen dimensiones reales y, cuando hay proveedor disponible, se describe la imagen mediante visión. Se respeta el texto alternativo vacío de elementos decorativos y la imagen principal no se carga de forma diferida. Esta versión no comprime ni convierte archivos de imagen.
- GEO diferencia menciones en respuestas de modelos de citas con URLs devueltas por el proveedor. No reproduce automáticamente el producto ChatGPT Search ni Google AI Overviews. GSC conserva la procedencia de los datos y los totales de tráfico se solicitan separados de los listados de consultas más frecuentes.

## Preparación en el servidor

Las unidades incluidas usan `/root/denzo-seo`, como las unidades anteriores del proyecto. Si tu instalación está en otra ruta o usa otro usuario, adapta las cinco unidades antes de copiarlas.

1. Desactiva ejecuciones programadas anteriores. Espera a que terminen los trabajos existentes y comprueba las colas RQ. No mezcles trabajos encolados con el formato antiguo de `run_agent_job(tenant_id, agent_name)` con esta versión, que recibe un identificador persistente. Si queda un trabajo bloqueado, revísalo antes de la actualización; no vacíes las colas de otros proyectos.
2. Detén los servicios de DENZO después de que no quede trabajo activo. Conserva los nombres e instancias de los trabajadores del auditor que ya tengas.
3. Haz una copia de la base de datos, de `.env` y de las unidades actuales. Usa la API `sqlite3.Connection.backup()` o la orden `.backup` de SQLite si hay procesos que todavía puedan escribir. No copies únicamente `denzo.db` mientras existan escrituras WAL.
4. Mantén `SECRET_KEY` y `DENZO_ENCRYPTION_KEY`: sustituir la clave de cifrado impediría leer las credenciales existentes. No publiques `.env` ni la base de datos en GitHub.

Con el servidor parado y la copia comprobada:

```bash
cd /root/denzo-seo
git status --short
git fetch origin
git checkout codex/platform-reliability
git pull --ff-only origin codex/platform-reliability
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pytest ruff
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check denzo --select F821
.venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(); from denzo.db import init_db; init_db()'
```

No descartes cambios locales del servidor para hacer el checkout: revísalos y consérvalos si `git status` muestra modificaciones.

En `.env`, comprueba:

```dotenv
DENZO_EXECUTOR=rq
REDIS_URL=redis://localhost:6379/0
DENZO_DB_PATH=/root/denzo-seo/data/denzo.db
```

Usa la URL real de Redis si difiere. Web, director, trabajadores y programador deben acceder a la **misma base SQLite en el mismo servidor**. Estas unidades no habilitan una arquitectura de varios servidores con bases independientes. Las migraciones son aditivas y se ejecutan también al arrancar la web.

## Servicios

```bash
cp services/denzo-web.service /etc/systemd/system/
cp services/denzo-worker.service /etc/systemd/system/
cp services/denzo-director.service /etc/systemd/system/
cp services/denzo-scheduler.service /etc/systemd/system/
cp services/denzo-auditor@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now denzo-web denzo-worker denzo-director denzo-scheduler
systemctl status denzo-web denzo-worker denzo-director denzo-scheduler --no-pager
```

Reinicia además las instancias del auditor que ya utilizabas, por ejemplo `denzo-auditor@1`. Su servicio ahora utiliza el mismo entorno `.venv`. Conserva la instalación de Chrome/Lighthouse que necesita el auditor; instalar los paquetes Python no instala estos componentes.

El director escucha `denzo-director` y los agentes escuchan `denzo-seo`. Ambos trabajadores son necesarios: el director espera a otros agentes y no puede ocupar el único consumidor de su misma cola.

El programador verifica las publicaciones pendientes y consulta las programaciones cada 30 segundos. El botón de Lite activa una programación real a las 09:00 UTC por defecto. Los interruptores antiguos almacenados en `settings.autopilot` no activan tareas por sí solos: vuelve a activar el botón tras la actualización. Se prepara como máximo un borrador por ejecución diaria y la publicación automática se limita a una revisión aprobada por ventana de 24 horas. Puede detenerse para pedir revisión o esperar un despliegue.

## WordPress

Copia la carpeta `integrations/wordpress/denzo-seo` en `wp-content/plugins/denzo-seo` y activa **DENZO SEO Connector**. Configura en DENZO la URL WordPress, el usuario y su contraseña de aplicación.

El conector expone metadatos SEO mediante REST y los imprime en la web. Incluye integración con los títulos y descripciones de Yoast y Rank Math. Las entradas de tipo blog/article/post se publican en `posts`; los demás tipos, en `pages`. No se ha validado contra todos los temas ni contra otros plugins SEO.

Comprueba que `/wp-json/denzo-seo/v1/capabilities`, con autenticación, devuelve `seo_metadata: true` y permisos de publicación adecuados. Publicar `llms.txt` y la clave IndexNow requiere un usuario con `manage_options` (administrador). El conector sirve ambos recursos como texto plano mediante WordPress; el servidor debe enviar esas rutas al controlador de WordPress.

Aprueba una página de prueba con hechos reales, ejecuta el publicador y revisa su HTML público: título, descripción, canonical, schema, imágenes y `meta[name="denzo-revision"]`. Debe terminar como `published` / `verified`. Purga la caché del sitio si el publicador sigue viendo una versión anterior.

## GitHub y Next.js

Se admiten HTML estático y el adaptador Next.js existente del proyecto. Para Next.js se exige `app/[locale]` bajo `github_path_prefix`; genera rutas `/en/<tipo>/<slug>` y coloca los recursos estáticos bajo `public/`. No interpreta automáticamente cualquier estructura de Next.js, otro idioma por defecto ni cualquier CMS. Adapta el publicador antes de usar otra estructura.

El servidor de despliegue debe ejecutar el build de la rama configurada y servir la URL `pages_domain`. Los archivos ya existentes sin propiedad en `managed_paths` se protegen, incluidos `sitemap.xml`, `robots.txt` y `llms.txt`. Si tu sitio tiene un sitemap propio, incorpora las nuevas rutas en su generador; no elimines su protección para sustituirlo a ciegas.

La verificación solo confirma que se sirve el marcador de la revisión; no sustituye las pruebas de navegación, el build ni Search Console. Si una publicación queda pendiente, comprueba primero el despliegue, la URL, la caché y los registros. No borres la reserva ni vuelvas a crear una entrada en WordPress sin comprobar si ya existe.

## Comprobación operativa

- Entra con dos cuentas de cliente: cada una debe ver solo sus proyectos y no debe poder abrir las URLs de la otra.
- Genera un borrador, revísalo y apruébalo. Cambiarlo debe requerir una nueva evaluación y aprobación. Las aprobaciones antiguas basadas solo en una etiqueta no autorizan nuevas publicaciones.
- Arranca un agente dos veces y comprueba que solo haya una ejecución. Al pulsar detener, la ejecución queda solicitada hasta que el agente llega a un punto de cancelación; una petición externa ya enviada puede terminar antes.
- Publica una página de prueba por cada adaptador utilizado. Comprueba que un fallo o un despliegue todavía pendiente no figura como una nueva publicación verificada.
- Activa la programación de un cliente y comprueba la fecha persistida y los registros de `denzo-scheduler`. No se aprueba contenido automáticamente.
- Revisa las conexiones de GSC y proveedores de IA con datos del cliente. Las pruebas automáticas sustituyen las respuestas externas; no acreditan que tus claves o permisos actuales funcionen.

## Alcance de vídeo e indexación

YouTube utiliza transferencia de bytes mediante una sesión reanudable y solo registra éxito al recibir un identificador de vídeo. Se conserva la sesión para reintentos. El adaptador necesita una URL de un MP4 o WebM terminado, de hasta 128 MiB. Los proveedores de generación que solo devuelven un identificador de tarea todavía requieren completar su integración asíncrona; no se contabilizan como vídeos generados. No ejecutes este flujo en producción sin revisar el material, la cuenta y los permisos.

IndexNow se utiliza tras comprobar que la clave de texto es accesible. «Enviado» no significa «indexado». La API de indexación de Google se limita a los tipos admitidos por Google; el contenido ordinario utiliza descubrimiento por sitemap. `llms.txt` aporta una descripción accesible, no garantiza visibilidad ni citas. Referencias: [Google Indexing API](https://developers.google.com/search/apis/indexing-api/v3/using-api), [IndexNow](https://www.indexnow.org/documentation), [subidas reanudables de YouTube](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol).

## Reversión

Si falla la comprobación operativa, detén los servicios nuevos, conserva la base actual para diagnóstico y restaura **juntos** el commit, las unidades, `.env` y la copia de la base previa a la actualización. El código anterior no conoce las nuevas reservas ni sus estados. Una reversión del servidor no despublica contenido que ya haya recibido WordPress o el repositorio de un cliente; compruébalo por separado antes de reanudar trabajos.

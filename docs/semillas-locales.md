# Datos locales reproducibles (T11/T12)

Estas semillas son un apoyo para desarrollo local. Todo el catálogo incluido en
este commit tiene `status: DRAFT`; los puntos, factores, nombres de acciones y
clanes no representan valores aprobados por Producto o la institución.

## Preparar una base local

Desde la raíz del checkout, copia `.env.example` a `.env` y conserva
`DJANGO_ENV=dev` y `DJANGO_DEPLOYED=false`. Después inicia PostgreSQL 18 y
aplica las migraciones:

Si `docker compose` no está disponible, usa `docker-compose` en estos comandos.

```sh
docker compose up -d db
set -a; . ./.env; set +a
python app/manage.py migrate
```

## Catálogo provisional (T11)

La fuente versionada es
`app/green_iteso/actions/fixtures/catalog_draft.json`. Puedes cargarla con:

```sh
python app/manage.py load_catalog
# Alias compatible con el ticket T11:
python app/manage.py seed_reference
```

El importador valida todo el JSON antes de abrir la transacción. Los códigos
son claves estables y los UUID de referencia son deterministas entre
checkouts. Si un código ya existe, sus datos actuales se conservan para no
pisar una edición hecha desde Admin; por ello una actualización de catálogo
requiere una decisión y una migración/importación versionada explícita.
El campo `validation_type` usa únicamente `NONE` o `PHOTO` según el ERD
aprobado. La asignación de un tipo a cada acción de esta semilla sigue siendo
DRAFT, igual que sus puntos, límites y factores ambientales.

La semilla provisional se rechaza cuando `DJANGO_DEPLOYED=true`, así que no
puede cargar accidentalmente valores DRAFT en staging o producción. También
rechaza un `DATABASE_URL` cuyo host no sea local (`127.0.0.1`, `localhost`,
`::1` o el servicio Compose `db`) y verifica que la conexión PostgreSQL no use
TLS. Esta segunda comprobación evita que un alias local hacia un proxy o túnel
remoto permita escribir por accidente en Neon. La carga también falla si un
código o ID determinista ya pertenece a un registro con otra identidad. Para
Neon dev existe un comando separado, descrito abajo; esta ruta DRAFT nunca se
habilita para bases compartidas.

## Demo sintético (T12)

Después de migrar, crea los datos de ejemplo:

```sh
python app/manage.py bootstrap_dev
# Alias compatible:
python app/manage.py seed_demo
```

El comando crea 20 usuarios sintéticos, clanes institucionales DRAFT, dos
clanes privados y membresías. Incluye cuatro campañas: una global activa,
una global futura, una global terminada con ocho participantes y una privada
activa con ocho participantes de su clan. Las cinco misiones permiten probar
progreso y listas con y sin participación. También crea 24 logs
`APPROVED`, `PENDING_AUDIT` y `REJECTED`. Los IDs e idempotency keys son
deterministas; ejecutarlo varias veces conserva una sola copia, pero si un ID
determinista existente no coincide con la identidad demo esperada, el comando
se detiene en vez de adoptar o modificar el registro. Las fotos son
solo claves de objeto locales (`demo-only/...jpg`), nunca URLs firmadas ni
archivos reales.

Por defecto, las ventanas de campañas se calculan con la hora actual. Usa
`--as-of` sólo para pruebas con el reloj fijado en la misma fecha: una fecha
futura usada hoy puede dejar un estado visible incompatible con las ventanas
cuando se integre la sincronización de campañas de PR #111. La semilla es
idempotente y una segunda ejecución no mueve las fechas de campañas existentes;
para escenarios temporales nuevos usa una base local descartable recién creada.

En local, cada rerun recalcula los puntos ganados desde los logs `APPROVED`
bajo bloqueo del perfil y aplica esa diferencia al saldo disponible. Conserva
así los puntos ya gastados, incluso si cambiaste el estado de un log. Si el
saldo previo es inválido o la corrección dejaría un saldo negativo, todo el
bootstrap se revierte con `CommandError`; no devuelve puntos gastados ni borra
historial. Usa la base local aislada descrita abajo para obtener un escenario
limpio. En Neon dev se conservan los incrementos de las filas nuevas; esta
reconciliación local no se aplica a bases compartidas.

### Compatibilidad con demos anteriores

La selección de acciones usa `is_active` de la fixture, no el estado editable
de la base. Así conserva las identidades al resembrar tras una edición de
Admin. Las acciones inactivas de la fixture no reciben misiones ni logs nuevos;
un catálogo enteramente inactivo sigue disponible sólo como escenario DRAFT.
Los ejemplos `PENDING_AUDIT` usan una acción `PHOTO` con una clave de evidencia
sintética; si el catálogo no ofrece ninguna, esos ejemplos quedan `APPROVED`.

Las versiones anteriores podían crear una misión privada de `demo-retired`
y logs pendientes sobre acciones `NONE`. Esta corrección cambia su selección
determinista. Si ya sembraste esa versión, un rerun puede detenerse con
`Demo mission identity collision` o `Demo action-log identity collision`:
la transacción se revierte y los snapshots existentes se conservan. No cambies
IDs, desactives las comprobaciones ni edites el historial para forzar el rerun.

Para probar la versión nueva en local sin borrar tu demo, usa otro proyecto
Compose y un puerto local libre (por ejemplo, `55447`), tras cargar el entorno
local de la sección anterior:

```sh
COMPOSE_PROJECT_NAME=greeniteso-seed-v2 POSTGRES_PORT=55447 docker compose up -d --wait db
export DATABASE_URL="postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@127.0.0.1:55447/$POSTGRES_DB"
python app/manage.py migrate
python app/manage.py bootstrap_dev
```

En Neon dev, la carga registrada el 2026-09-28 necesita una revisión de esas
filas antes de resembrar: identificar los UUID demo, misiones y contribuciones
afectadas, comparar los snapshots y saldos actuales, y acordar una reparación
específica con el operador. Este cambio no elimina ni reescribe datos cloud y
no incluye un comando de reparación; la corrección de la semilla no acredita
que el dataset existente ya esté corregido.

## Datos sintéticos en Neon dev (opt-in, T11/T12)

El catálogo DRAFT local y `bootstrap_dev` siguen siendo exclusivamente locales.
`seed_neon_dev` usa una fixture DRAFT **distinta**, fija y versionada:
`app/green_iteso/actions/fixtures/catalog_dev_synthetic_v1.json`. Sólo está
autorizada para Neon dev; sus acciones, puntos e impactos son ficticios y no
representan ratificación de Producto. Incluye cuatro carreras autodeclaradas
para la demo: Ingeniería en Desarrollo de Software, Ingeniería en Sistemas
Computacionales, Ingeniería en Mecatrónica e Ingeniería en Ciberseguridad.
No acredita la carrera oficial de una persona ni constituye catálogo aprobado.
El comando no acepta rutas arbitrarias. Calcula el SHA-256 del archivo y lo
compara con `NEON_DEV_SYNTHETIC_CATALOG_SHA256` suministrado en el entorno de
ejecución; un pin ausente o distinto detiene la carga antes de escribir.
Calcula el pin con `shasum -a 256` sobre el archivo revisado. No reutilices
`NEON_DEV_APPROVED_CATALOG_SHA256`: ese pin pertenece sólo a `release_catalog`.

Usa únicamente el endpoint pooled canónico `dev`, TLS `verify-full` y el rol
`greeniteso_dev_app`. Mantén la URL en el entorno de ejecución o secreto
aprobado; no la guardes en el repo ni uses `DATABASE_URL_UNPOOLED`:

```sh
DJANGO_ENV=dev \
DJANGO_DEPLOYED=true \
DJANGO_CONNECTION_ROLE=app \
NEON_DEV_SYNTHETIC_CATALOG_SHA256="$DEV_SYNTHETIC_CATALOG_SHA256" \
DATABASE_URL="$NEON_DEV_DATABASE_URL" \
python app/manage.py seed_neon_dev \
  --confirm-target dev
```

El comando comprueba el ambiente, host, rol, TLS configurado y TLS de la
conexión; exige `--confirm-target dev`; valida el catálogo completo antes de
escribir; y carga catálogo y demo en una transacción. Los usuarios sintéticos
son `STUDENT` (nunca `ADMIN`/`STAFF`), con correo `example.invalid` y sin
identidad Microsoft Entra ni contraseña. Los clanes de carrera usan los UUID
estables de la fixture cuando no existe uno del mismo nombre. Si onboarding ya
creó un clan institucional público sin creador, el seed adopta su UUID y conserva
su descripción, membresías, perfiles y saldos ajenos a la demo. Una fila dada de
baja se restaura si es la única candidata; si existe una fila activa del mismo
nombre, se usa esa y se transfieren únicamente las referencias y puntos demo
verificados. Varios candidatos dados de baja sin uno activo son ambiguos: el
comando aborta sin escribir y requiere revisión del operador. También rechaza
colisiones con clanes privados o identidades estables editadas.

La reconciliación v1 mueve únicamente los 20 perfiles demo, sus membresías y
los 24 logs sintéticos cuya identidad completa se comprueba antes de escribir.
Incluye los logs `APPROVED`: se trasladan sus puntos entre clanes sin cambiar
saldos ni atribución histórica de otras personas. Los tres clanes legados
`Demo institutional clan` se dan de baja lógica si quedan sin perfiles,
membresías ni logs, para quitarlos del selector. Si conservan referencias
ajenas, se mantienen; una baja manual posterior no rompe las siguientes
corridas. No se borran filas. Los estados parciales, incompatibles o editados
se rechazan transaccionalmente. El candado advisory serializa las corridas;
un conflicto concurrente requiere reintentar con el estado confirmado.

Los UUID demo y las claves siguen siendo deterministas, y la segunda ejecución
es idempotente. Al cambiar los bytes de la fixture, el operador debe revisar
el diff y **actualizar `NEON_DEV_SYNTHETIC_CATALOG_SHA256`** con el digest de ese
archivo exacto antes de ejecutar. Este cambio no actualiza el secreto ni ejecuta
el seed en Neon. Esa ejecución requiere autorización explícita y confirmar
proyecto, rama `dev`, base y estado previo esperado. Esta operación no forma
parte de la migración al merge ni de un deploy automático. No se cargan semillas
en staging/preprod ni en production. El comando se probó con PostgreSQL 18
local y se ejecutó con autorización en Neon `dev` el 2026-09-28; el
[registro de T12](https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Infra/issues/12#issuecomment-5880568568)
documenta el dataset sintético y confirma que staging/production siguen sin
datos demo. Esta ejecución manual no implica aprobación del catálogo
institucional ni sustituye la revisión/merge de este PR.

Estos registros sirven para consultar relaciones y estados, pero no permiten
iniciar sesión como esos usuarios: no se crea un bypass de autenticación en
Neon dev. Los flujos Admin/Staff y propuestas de campaña necesitarán
escenarios adicionales cuando se integren sus PRs de modelo y API; este
dataset no acredita esas historias por sí solo.

## Liberación aprobada solo de catálogo

`release_catalog` publica únicamente categorías, acciones y clanes
institucionales aprobados. Usa la ruta fija y versionada
`app/green_iteso/actions/fixtures/catalog_approved_v1.json`; no admite una
fixture elegida en la línea de comandos. El archivo se mantiene ausente hasta
que Product registre la aprobación. El comando también falla si el pin externo
no está configurado o no coincide con los bytes exactos de la fixture.

Configura el SHA-256 en el entorno protegido del destino:
`NEON_DEV_APPROVED_CATALOG_SHA256`,
`NEON_STAGING_APPROVED_CATALOG_SHA256` o
`NEON_PRODUCTION_APPROVED_CATALOG_SHA256`. La metadata `approval` del JSON es
trazabilidad, no evidencia por sí sola de signoff. Los nombres de roles se
validan según el mapa de Infra: `greeniteso_dev_app`,
`greeniteso_staging_app` y `greeniteso_production_app`.

Ejemplo para dev (usa el destino correspondiente en los otros ambientes):

```sh
DJANGO_ENV=dev \
DJANGO_DEPLOYED=true \
DJANGO_CONNECTION_ROLE=app \
DATABASE_URL="$NEON_DEV_DATABASE_URL" \
python app/manage.py release_catalog --confirm-target dev
```

El comando valida ambiente, confirmación explícita, host pooled canónico,
rol app, `sslmode=verify-full` y TLS de la conexión. La carga es atómica e
idempotente: un rerun exacto no cambia filas; los códigos o identidades que
colisionan y las filas editadas con contenido distinto detienen la carga y
revierten todo. Las actualizaciones no se aplican silenciosamente. No crea
usuarios, actividad, campañas ni datos demo. Es una ejecución manual fuera del
flujo de migración/despliegue; este trabajo no ejecuta el comando contra Neon,
especialmente en production. No agregues valores ni configures pins sin la
decisión registrada de Product.

## Identidad y Admin local

Los usuarios demo no tienen identidad Microsoft Entra ni contraseña. Eso evita
simular un login institucional y evita publicar una contraseña universal. Para usar Django
Admin en local, crea tu propia cuenta administrativa:

```sh
python app/manage.py createsuperuser
```

Admin está disponible únicamente con `DEBUG=true` en el entorno local. En
staging y producción la autenticación institucional se verifica con Microsoft
Entra; estas semillas no crean un bypass ni
un endpoint de autenticación alternativo.

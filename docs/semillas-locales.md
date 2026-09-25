# Datos locales reproducibles (T11/T12)

Estas semillas son un apoyo para desarrollo local. Todo el catálogo incluido en
este commit tiene `status: DRAFT`; los puntos, factores, nombres de acciones y
clanes no representan valores aprobados por Producto o la institución.

## Preparar una base local

Desde la raíz del checkout, copia `.env.example` a `.env` y conserva
`DJANGO_ENV=dev` y `DJANGO_DEPLOYED=false`. Después inicia PostgreSQL 18 y
aplica las migraciones:

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
python app/manage.py bootstrap_dev --as-of 2030-01-15T12:00:00+00:00
# Alias compatible:
python app/manage.py seed_demo --as-of 2030-01-15T12:00:00+00:00
```

El comando crea 20 usuarios sintéticos, clanes institucionales DRAFT, dos
clanes privados, membresías, una campaña global activa, misiones, progreso y
logs `APPROVED`, `PENDING_AUDIT` y `REJECTED`. Los IDs e idempotency keys son
deterministas; ejecutarlo varias veces conserva una sola copia, pero si un ID
determinista existente no coincide con la identidad demo esperada, el comando
se detiene en vez de adoptar o modificar el registro. Las fotos son
solo claves de objeto locales (`demo-only/...jpg`), nunca URLs firmadas ni
archivos reales.

`--as-of` fija el reloj de la campaña para que una demo repetible no dependa de
la fecha actual. Si se omite, se usa la hora actual para una ventana relativa
de 30 días.

## Datos sintéticos en Neon dev (opt-in, T11/T12)

El catálogo DRAFT y `bootstrap_dev` siguen siendo exclusivamente locales. La
única ruta para Neon es `seed_neon_dev`, invocada manualmente **después de que
Producto ratifique el catálogo**. No hay una fixture aprobada en el repositorio
hasta que esa decisión exista; por tanto, el comando falla por archivo ausente.
El comando sólo lee la ruta fija y versionada
`app/green_iteso/actions/fixtures/catalog_approved.json`, sin aceptar un archivo
arbitrario por argumento. El archivo debe estar marcado `status: APPROVED` e
incluir `approval.approved_by`, `approval.reference` y `approval.approved_at`
como timestamp ISO-8601 con zona horaria. Esos campos son trazabilidad, no una
prueba independiente de la ratificación: la fixture debe añadirse mediante un
PR revisado que vincule la decisión de Producto. Los tests usan un payload
aprobado sintético sólo como fixture; eso no representa ratificación.
Además, un operador autorizado sólo podrá habilitar la carga después de que
Producto registre esa aprobación: deberá fijar
`NEON_DEV_APPROVED_CATALOG_SHA256` en el entorno GitHub `dev` con el SHA-256
exacto del archivo aprobado. El comando compara ese pin externo antes de abrir
la transacción; no existe hoy una fixture ni un pin aprobado, y no se deben
configurar hasta que exista la evidencia de Producto.

Usa únicamente el endpoint pooled canónico `dev`, TLS `verify-full` y el rol
`greeniteso_dev_app`. Mantén la URL en el entorno de ejecución o secreto
aprobado; no la guardes en el repo ni uses `DATABASE_URL_UNPOOLED`:

```sh
DJANGO_ENV=dev \
DJANGO_DEPLOYED=true \
DJANGO_CONNECTION_ROLE=app \
NEON_DEV_APPROVED_CATALOG_SHA256="$APPROVED_CATALOG_SHA256" \
DATABASE_URL="$NEON_DEV_DATABASE_URL" \
python app/manage.py seed_neon_dev \
  --confirm-target dev \
  --as-of 2030-01-15T12:00:00+00:00
```

El comando comprueba el ambiente, host, rol, TLS configurado y TLS de la
conexión; exige `--confirm-target dev`; valida el catálogo completo antes de
escribir; y carga catálogo y demo en una transacción. Los usuarios sintéticos
son `STUDENT` (nunca `ADMIN`/`STAFF`), con correo `example.invalid` y sin
identidad Firebase ni contraseña. IDs y claves son deterministas, los choques
se rechazan y una segunda ejecución es idempotente. Esta operación no forma
parte de la migración al merge ni de un deploy automático. No se cargan semillas
en staging/preprod ni en production. **El comando se implementó y probó sólo con
PostgreSQL 18 local; no se ejecutó contra Neon.**

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

Los usuarios demo no tienen `firebase_uid` ni contraseña. Eso evita simular un
login de Firebase y evita publicar una contraseña universal. Para usar Django
Admin en local, crea tu propia cuenta administrativa:

```sh
python app/manage.py createsuperuser
```

Admin está disponible únicamente con `DEBUG=true` en el entorno local. En
staging y producción la autenticación de la aplicación sigue siendo Firebase
verificado para cuentas institucionales; estas semillas no crean un bypass ni
un endpoint de autenticación alternativo.

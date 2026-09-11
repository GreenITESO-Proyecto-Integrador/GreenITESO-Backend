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
`::1` o el servicio Compose `db`), para evitar modificar una rama de Neon
compartida por error. En esos
ambientes solo una carga futura con datos aprobados y un procedimiento de
release podrá ser habilitada por el equipo responsable.

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
deterministas; ejecutarlo varias veces conserva una sola copia. Las fotos son
solo claves de objeto locales (`demo-only/...jpg`), nunca URLs firmadas ni
archivos reales.

`--as-of` fija el reloj de la campaña para que una demo repetible no dependa de
la fecha actual. Si se omite, se usa la hora actual para una ventana relativa
de 30 días.

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

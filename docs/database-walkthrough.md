# Recorrido reproducible de base de datos

Esta guía prepara un clon local con PostgreSQL 18 en Docker, aplica las migraciones y muestra un cambio de modelo. No usa Neon ni datos compartidos.

## Requisitos

Instala Git, Docker Desktop con Compose y `make`. En Windows puedes usar WSL 2 con integración de Docker. Comprueba:

```sh
git --version
docker compose version
make --version
```

Si tu instalación sólo tiene el binario separado, sustituye `docker compose` por `docker-compose` en comandos Docker explícitos. Los targets `make` eligen automáticamente el binario disponible.

## Clonar y arrancar

En una terminal de host nueva:

```sh
git clone -b dev https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Backend.git
cd GreenITESO-Backend
make init-local
make compose-up
```

`make init-local` crea `.env` desde `.env.example` sólo si todavía no existe;
no sobrescribe una configuración local. Si los puertos predeterminados
`APP_PORT=8000` o `POSTGRES_PORT=5432` están ocupados, edita esos valores en
`.env` antes de arrancar, por ejemplo `APP_PORT=18480` y
`POSTGRES_PORT=15492`.

Deja esa terminal abierta. Compose construye la aplicación y mantiene el volumen local de PostgreSQL. En una segunda terminal, entra de nuevo a `GreenITESO-Backend` y ejecuta:

```sh
make migrate
make db-smoke
make test
```

En la revisión actual la primera migración muestra 31 migraciones aplicadas;
ese número puede crecer cuando se agreguen migraciones aprobadas. `db-smoke`
debe mostrar `DB_SMOKE OK`, `SELECT 1: OK`, conteos ORM en cero y
`ssl_cliente: inactivo`; ese estado TLS es esperado para PostgreSQL local. La
suite terminó con `28 passed` en esta copia; el número de pruebas puede crecer.
El CI usa la misma idea con una base temporal y no actualiza Neon.

## Crear un registro de demostración

Con los servicios levantados, crea una categoría sintética desde la segunda terminal:

```sh
docker compose exec -T app python app/manage.py shell -c 'from green_iteso.actions.models import ActionCategory; c, _ = ActionCategory.objects.get_or_create(code="DEMO", defaults={"name":"Reciclaje"}); print(c.code, c.name)'
```

Si usas el fallback, cambia sólo el prefijo por `docker-compose exec -T`. La salida esperada incluye `DEMO Reciclaje`.

## Cambiar el modelo y generar la migración

Crea una rama de trabajo:

```sh
git switch -c demo/category-order
```

Edita exactamente `app/green_iteso/actions/models.py`. Dentro de `ActionCategory`, agrega:

```python
    display_order = models.PositiveIntegerField(default=0)
```

Colócalo dentro de `ActionCategory`, antes de `class Meta`, conservando los
campos existentes.

Genera la migración con una sola línea. El comando visual es compatible con Compose moderno:

```sh
docker compose exec -T app python app/manage.py makemigrations actions
```

Con el fallback, usa `docker-compose exec -T app python app/manage.py makemigrations actions`. Django debe crear `app/green_iteso/actions/migrations/0006_actioncategory_display_order.py` con `Add field display_order to actioncategory`. Revisa el archivo y el modelo juntos. Esta migración es parte del ejemplo local y no se debe commitear como cambio aprobado del esquema.

## Aplicar, consultar y comprobar

Aplica la migración y verifica que el modelo ya no tenga cambios pendientes:

```sh
make migrate
make makemigrations-check
```

Consulta el registro demo y el nuevo valor:

```sh
docker compose exec -T app python app/manage.py shell -c 'from green_iteso.actions.models import ActionCategory; c = ActionCategory.objects.get(code="DEMO"); print(c.code, c.name, "display_order=", c.display_order)'
```

La salida esperada de `make makemigrations-check` es `No changes detected`.
Después consulta el registro y espera `DEMO Reciclaje display_order= 0`.
Ejecuta las pruebas otra vez:

```sh
make db-smoke
make test
```

El smoke check es de lectura y no aplica migraciones. La suite debe terminar con `28 passed` en la copia de demostración.

## Cerrar el ejercicio temporal

Mientras conserves este ejercicio, conserva **juntos** el campo del modelo y su
archivo de migración. No elimines sólo uno. Si el equipo decide incorporar un
cambio real, modelo y migración se incluyen juntos en un PR revisado.

Para volver al esquema inicial en **este clon dedicado al ejercicio**, sin otros
cambios ni migraciones posteriores, deshaz primero la migración de ejemplo con
los servicios todavía levantados:

```sh
docker compose exec -T app python app/manage.py migrate actions 0005_alter_actioncategory_table_alter_actionlog_table_and_more
```

Este destino es el predecesor de `0006_actioncategory_display_order` en la
revisión de esta guía. Si tu historial ya es distinto, no copies ese destino a
ciegas: revisa la migración generada y sus dependencias. No uses este procedimiento
en un ambiente compartido. Revertir este `AddField` descarta los valores del
campo de ejemplo, pero conserva la categoría.

Después, en el editor, elimina únicamente la línea `display_order` que agregaste
a `ActionCategory` y el archivo generado
`app/green_iteso/actions/migrations/0006_actioncategory_display_order.py`.
No borres migraciones previas ni otros cambios propios. Verifica:

```sh
make makemigrations-check
make db-smoke
make test
git status --short
```

Debes obtener `No changes detected`, smoke correcto, pruebas pasando y ningún
cambio del ejercicio pendiente en Git. Ya puedes volver a `dev`. Si prefieres
mantener el ejercicio para repetir la demostración, omite esta reversión y deja
juntos el modelo, la migración y su base local.

## Limpieza

Conserva el volumen local para poder repetir el recorrido y detén Compose cuando termines:

```sh
make compose-down
```

Este comando detiene contenedores y conserva datos. No uses comandos de desarrollo contra Neon, no copies credenciales cloud al `.env` y no borres el volumen de otro checkout.

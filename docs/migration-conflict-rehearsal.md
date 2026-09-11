# Rehearsal de conflicto de migraciones (T10)

Este rehearsal reproduce un caso pequeño y desechable: dos equipos parten de
la migración compartida `0001_initial` y agregan, en paralelo, las hojas
`0002_team_a` y `0002_team_b`. Cada hoja agrega una columna distinta y
actualiza la fila de ejemplo. Django rechaza el grafo porque hay dos hojas.
Después se crea `0003_merge_team_leaves`, una migración de merge sin
operaciones, y se verifica el esquema y la fila resultantes en PostgreSQL 18.

La fixture se escribe bajo `TemporaryDirectory`; no agrega tablas ni
migraciones al proyecto Django de Backend. El script arranca un contenedor
`postgres:18` en un puerto efímero, lo elimina al terminar y nunca imprime la
URL de conexión.

## Reproducir

Desde la raíz de este worktree:

```sh
uv run --python 3.14 \
  --with 'Django==5.2.17' \
  --with 'psycopg[binary]==3.3.5' \
  scripts/rehearse-migration-conflict.py
```

El comando requiere Docker y uv en el host. Siempre crea su propio PostgreSQL
18 desechable: no acepta URLs de una base existente. `--keep-container` permite
conservar únicamente ese contenedor para inspección. SQLite no es un sustituto
válido para este ejercicio.

## Evidencia observada

La ejecución completa produce un fallo deliberado antes de agregar el merge y
termina en verde después de aplicarlo:

```text
[database] PostgreSQL server_version_num=180006
[before migrate --plan (expected conflict)] exit=1
CommandError: Conflicting migrations detected; multiple leaf nodes in the migration graph: (0002_team_a, 0002_team_b in rehearsal_records).
To fix them run 'python manage.py makemigrations --merge'
[before makemigrations --check --dry-run (expected conflict)] exit=1
CommandError: Conflicting migrations detected; multiple leaf nodes in the migration graph: (0002_team_a, 0002_team_b in rehearsal_records).
[after merge migrate] exit=0
Applying rehearsal_records.0001_initial... OK
Applying rehearsal_records.0002_team_b... OK
Applying rehearsal_records.0002_team_a... OK
Applying rehearsal_records.0003_merge_team_leaves... OK
[after migrate --plan] exit=0
[after makemigrations --check --dry-run] exit=0
[after snapshot] columns=['created_at', 'id', 'label', 'team_a_code', 'team_b_code']
[after snapshot] seed_row=('seed', 'A-READY', 'B-READY')
RESULT: conflict reproduced, compatible merge applied, schema/data match expected
```

Los mensajes completos pueden variar entre versiones menores de Django, pero
los checks exigidos por el script son estables: ambas comprobaciones fallan
con las dos hojas, y después del merge `migrate --plan` y
`makemigrations --check --dry-run` terminan con código cero, con las cinco
columnas y los tres valores esperados en la fila.

## Regla operativa

Una migración que ya fue aplicada en un ambiente compartido se conserva con
su nombre, dependencias y operaciones. Nunca se renumera, renombra, borra ni
se oculta su divergencia con `--fake`. Si las hojas paralelas son compatibles,
se conserva cada operación y se agrega una migración de merge que dependa de
ambas hojas, como en este rehearsal. Si una hoja todavía es exclusivamente
local y no se aplicó ni llegó a un ambiente compartido, el equipo puede
rebasear sobre la integración y regenerar una sola migración lineal; se debe
revisar de nuevo el SQL, el recorrido desde cero, el upgrade y los datos antes
del PR.

Si las operaciones dependen de un orden, de datos existentes o no son
compatibles, no se usa un merge vacío. Los dueños acuerdan una migración de
reconciliación explícita, con el procedimiento de corrección hacia adelante.

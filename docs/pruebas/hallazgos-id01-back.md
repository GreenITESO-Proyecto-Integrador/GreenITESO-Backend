# Hallazgos ID01: login institucional (backend)

ID01 = Identidad 1 en la división de trabajo: login con Microsoft (mock y Entra ID), dominio @iteso.mx, alta o actualización del usuario y rol por puesto.

Generado por `pytest` el 2026-11-22. Cada hallazgo tiene una
prueba que afirma el comportamiento correcto y se marca `xfail` mientras el
problema siga ahí.

## Resumen por integrante

| Integrante | Pendientes | Resueltos |
|---|---|---|
| DehBarca | 2 | 0 |
| Pardosito | 3 | 0 |

## Detalle

### 1. El login le quita el rol STAFF que asignó un administrador

- **Integrante:** DehBarca
- **Severidad:** alta
- **Estado:** pendiente
- **Qué pasa:** Un admin sube a STAFF a un alumno (PATCH /users/{id}/role/). En su siguiente login, el puesto 'Alumno' del directorio lo regresa a STUDENT. Solo el rol ADMIN está protegido en _get_or_create_user.
- **Qué hacer:** Si el último cambio de rol lo hizo un admin (hay un UserRoleAudit), no recalcular el rol en el login; o guardar un campo 'rol fijado por admin'.
- **Prueba:** `app/green_iteso/accounts/tests/login_institucional/test_hallazgos.py::test_rol_asignado_por_admin_sobrevive_al_login`

### 2. Un alumno con 'Profesor' en el puesto se vuelve STAFF

- **Integrante:** DehBarca
- **Severidad:** media
- **Estado:** pendiente
- **Qué pasa:** determine_initial_role busca palabras dentro del puesto. 'Alumno Asistente de Profesor' contiene PROFESOR y queda como STAFF aunque es alumno.
- **Qué hacer:** Revisar primero si el puesto empieza con ALUMNO/ESTUDIANTE antes de buscar palabras de personal, o usar los grupos de Entra en vez del texto.
- **Prueba:** `app/green_iteso/accounts/tests/login_institucional/test_hallazgos.py::test_alumno_asistente_de_profesor_es_student`

### 3. La base de datos acepta el mismo correo con distintas mayúsculas

- **Integrante:** Pardosito
- **Severidad:** media
- **Estado:** pendiente
- **Qué pasa:** User.email es único pero distingue mayúsculas: 'X@iteso.mx' y 'x@iteso.mx' pueden ser dos cuentas. El login normaliza a minúsculas, pero el admin de Django, un seed o un script pueden crear el duplicado.
- **Qué hacer:** Agregar un UniqueConstraint sobre Lower('email') con su migración, y normalizar el correo en UserManager.create_user.
- **Prueba:** `app/green_iteso/accounts/tests/login_institucional/test_hallazgos.py::test_correo_unico_sin_importar_mayusculas`

### 4. El filtro de dominio acepta correos con dos @

- **Integrante:** Pardosito
- **Severidad:** baja
- **Estado:** pendiente
- **Qué pasa:** is_institutional_email solo mira lo que va después de la última @, así que 'a@gmail.com@iteso.mx' pasa. Entra no emite correos así, pero el filtro por sí solo no lo bloquea.
- **Qué hacer:** Rechazar correos con más de una @ (email.count('@') != 1).
- **Prueba:** `app/green_iteso/accounts/tests/login_institucional/test_hallazgos.py::test_correo_con_dos_arrobas_no_es_institucional`

### 5. Los grupos de Microsoft se cortan en silencio después de 3 páginas

- **Integrante:** Pardosito
- **Severidad:** baja
- **Estado:** pendiente
- **Qué pasa:** EntraProvider._fetch_group_ids lee como máximo 3 páginas de Graph. Si hay más, guarda una lista incompleta como si fuera la lista completa.
- **Qué hacer:** Si queda un @odata.nextLink después del límite, devolver None ('no se sabe') para no sobrescribir los grupos guardados, y registrar un aviso.
- **Prueba:** `app/green_iteso/accounts/tests/login_institucional/test_hallazgos.py::test_grupos_incompletos_no_se_guardan_como_completos`

# Invitaciones a clanes con código de un solo uso (#165)

Las invitaciones a un clan privado se hacen con **códigos de un solo uso**, no
con enlaces. El enlace `/clans/<id>` que compartía el frontend se rompía en los
clanes `PRIVATE_INVITE`: un no-miembro no puede ver el clan (404), y `/join/`
solo crea una solicitud `PENDING`.

## Flujo

1. El **líder** genera un código: `POST /api/v1/clans/{id}/invitations/` →
   `201 {"id", "code": "K7P2-QX9M", "expires_at"}`. Es la única vez que la API
   devuelve el código completo.
2. El líder lo comparte por el medio que quiera (WhatsApp, en persona, etc.).
3. La persona invitada lo canjea: `POST /api/v1/clans/redeem-invitation/` con
   `{"code": "k7p2 qx9m"}` → `201` con la membresía (`clan`, `status:
   ACCEPTED`). No necesita conocer el id del clan.

| Endpoint | Quién | Respuesta |
|---|---|---|
| `POST /clans/{id}/invitations/` | líder | `201` código nuevo |
| `GET /clans/{id}/invitations/` | líder | códigos activos con `code_hint` (últimos 4 caracteres) |
| `DELETE /clans/{id}/invitations/{invitation_id}/` | líder | `204`, revoca el código |
| `POST /clans/redeem-invitation/` | cualquier usuario autenticado | `201` membresía `ACCEPTED` |

## Reglas

- **Un solo uso.** Expira a las **48 h** o cuando el líder lo revoca.
- **Máximo 20 códigos activos por clan.** Hay que revocar uno para generar más.
- **Solo el líder del clan** genera, lista y revoca códigos (#164). Un admin
  global no puede.
- **Solo clanes privados con privacidad `PRIVATE_INVITE`.** Los institucionales
  se asignan por carrera, y a un clan `PUBLIC` cualquiera puede unirse directo,
  así que crear un código ahí responde 400. Los códigos que ya existían siguen
  funcionando si el clan cambia a `PUBLIC`, y el líder puede listarlos y revocarlos.
- Canjear un código **une directamente** al clan: el código es la aprobación
  previa del líder. Se respeta el límite de **5 clanes privados** (BR-04).
- Si ya existía una solicitud `PENDING` o `REJECTED` para ese clan, esa fila
  pasa a `ACCEPTED`.
- Si quien canjea ya es miembro, o rebasaría el límite de BR-04, recibe un 400
  y **el código no se consume**.
- Un código desconocido, expirado, usado, revocado o de un clan disuelto
  recibe siempre el mismo `400 {"code": ["Invalid or expired invitation code."]}`,
  para no revelar qué códigos existen.
- Todos los errores del canje (código inválido, ya eres miembro, límite de
  BR-04) llegan en el mismo campo: `{"code": ["…"]}`.
- Un `invitation_id` que no es un UUID válido en `DELETE …/invitations/{id}/`
  responde 404.

## Formato y almacenamiento

- 8 caracteres del alfabeto Crockford base32 (sin `I`, `L`, `O`, `U`), unos
  40 bits de entropía, mostrados como `XXXX-XXXX`.
- La entrada se normaliza: mayúsculas, sin espacios ni guiones, `O`→`0`, `I`/`L`→`1`.
- Se genera con `secrets` (stdlib). No se usa una librería TOTP/HOTP: esas
  modelan un secreto compartido con una app autenticadora, no un código
  revocable que se entrega a una persona.
- En `accounts_clan_invitation` solo se guarda un HMAC-SHA256 del código
  (`salted_hmac`, con `SECRET_KEY` como llave) y los últimos 4 caracteres.
  Rotar `SECRET_KEY` invalida los códigos pendientes, lo que es aceptable con
  una vigencia de 48 h.
- El canje bloquea el clan, luego la invitación y luego al usuario (el mismo
  orden que `accept_join_request`), así que dos personas con el mismo código
  no pueden entrar las dos.

## Límite de intentos

`POST /clans/redeem-invitation/` está limitado a **10 intentos por minuto por
usuario y por proceso** (`ClanInviteRedeemThrottle`, rate
`clan_invite_redeem`). Hoy no hay `CACHES` configurado, así que el contador
vive en la memoria de cada proceso: con varios workers el límite real es 10 ×
`WEB_CONCURRENCY`. Es una segunda capa detrás de la entropía del código, no la
única defensa.

## Retención

Los códigos expirados, usados o revocados se conservan **30 días** para
auditoría (`INVITATION_RETENTION`). Cada vez que un clan genera un código nuevo
se borran los de ese clan que expiraron hace más de 30 días, así que la tabla
queda acotada por clan sin necesidad de un job periódico. En el admin de Django
las invitaciones son de solo lectura: no se pueden crear, editar ni borrar.

## Pendiente

- Notificar al líder cuando alguien canjea un código (T2-45, #19).
- Actualizar el contrato del SDD en la wiki: §3.1.5 todavía muestra
  `access_code` en `/join/`.

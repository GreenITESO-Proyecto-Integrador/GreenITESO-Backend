# Login institucional con Microsoft Entra ID (T2-10)

`POST /api/v1/auth/login/` autentica cuentas `@iteso.mx` directamente contra
Microsoft Entra ID (Azure AD) y emite el par de tokens JWT de la aplicación.
No se usa Firebase Authentication para el login: llamar a Microsoft Graph
directamente conserva datos institucionales (departamento, puesto, employee
ID, membresías de grupo) que Firebase no expone. Ver la discusión de la
decisión en el issue T2-10 y en `CLAUDE.md`.

## Flujo

1. El SPA inicia sesión con [MSAL.js](https://github.com/AzureAD/microsoft-authentication-library-for-js)
   usando auth-code + PKCE, con scopes `openid profile email offline_access
   User.Read`. Obtiene un `id_token` y un `access_token` de Graph.
   `GroupMember.Read.All` (para membresías de grupo) queda fuera del flujo
   por defecto: ver la nota al final de esta sección.
2. El SPA llama `POST /api/v1/auth/login/` con
   `{"id_token": "...", "access_token": "..."}`.
3. El backend verifica el `id_token` (firma RS256 contra el JWKS del tenant,
   `aud`, `iss`, `tid`, `exp`), luego llama a Microsoft Graph `/me` con el
   `access_token` para obtener nombre, `jobTitle`, `department`,
   `employeeId` y los IDs de grupo.
4. BR-01: solo se permite el dominio `@iteso.mx` (exacto, sin subdominios ni
   variantes); otro dominio responde `403 DOMAIN_NOT_ALLOWED`.
5. El usuario se crea en el primer login (rol `STUDENT` por defecto) o se
   actualiza si ya existía.
6. La respuesta trae `{access, refresh, user, created}`; `access`/`refresh`
   son JWT de `djangorestframework-simplejwt` (60 min / 7 días,
   provisional — T2-11 define los valores finales).

Microsoft Graph es *best-effort*: si Graph no responde, o el `access_token`
no tiene permiso para una llamada concreta (por ejemplo, membresías de grupo
sin `GroupMember.Read.All`), el login continúa con los claims del `id_token`
y conserva los últimos valores de perfil guardados; solo el `id_token`
decide la identidad y el dominio permitido. `EntraProvider._fetch_group_ids`
trata cualquier respuesta que no sea 200 como "no se pudo obtener" y deja
`microsoft_group_ids` vacío, sin fallar el login.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `MICROSOFT_AUTH_MODE` | `entra` (staging/production) o `mock` (solo `DJANGO_ENV=dev` y `DJANGO_DEPLOYED=false`; falla al arrancar en cualquier otro caso). |
| `MICROSOFT_TENANT_ID` | Tenant ID de Entra ID (GUID). Requerido en modo `entra`. |
| `MICROSOFT_CLIENT_ID` | Application (client) ID del registro del SPA. Requerido en modo `entra`. |
| `ALLOWED_EMAIL_DOMAIN` | Dominio institucional permitido (default `iteso.mx`). |

No se necesita client secret: el backend solo valida tokens ya emitidos al
usuario (public client / SPA), nunca actúa como confidential client.

## Registro de la aplicación en Entra ID

Confirmado sobre el tenant de ITESO (tenant ID `6f0348f2-e498-45c9-84f4-c6d81dcffdfe`,
obtenido sin ninguna cuenta con
`curl https://login.microsoftonline.com/iteso.mx/v2.0/.well-known/openid-configuration`,
campo `issuer`): el acceso a `entra.microsoft.com` está restringido para
cuentas sin rol de directorio ("Restrict access to Microsoft Entra admin
center" activo). Eso es independiente de si el tenant permite
autorregistro de apps; simplemente bloquea el portal para cuentas normales,
así que registrar la app **dentro** del tenant de ITESO sí depende de IT.

### Ruta sin IT: app multi-tenant en un tenant propio

No hace falta registrar nada dentro del tenant de ITESO. Un app **multi-tenant**
registrado en cualquier otro tenant (el tuyo propio, gratis, creado en minutos
en [entra.microsoft.com](https://entra.microsoft.com) con una cuenta personal)
puede aceptar logins de cuentas `@iteso.mx`, con el mismo mecanismo que usa
cualquier "Sign in with Microsoft" de terceros: el usuario de ITESO consiente
individualmente, sin que un admin de ITESO intervenga. El claim `tid` del
`id_token` resultante sigue siendo el tenant **del usuario** (el de ITESO), así
que la verificación de dominio/tenant en `EntraProvider` no cambia.

1. Crear (o usar) un tenant Entra propio, fuera de ITESO.
2. **App registrations** → **New registration** → *Supported account types*:
   **Accounts in any organizational directory (Any Microsoft Entra ID tenant
   - Multitenant)**. Plataforma **SPA**, con los redirect URIs del frontend.
3. Permisos delegados: `openid`, `profile`, `email`, `offline_access` y
   `User.Read` — ninguno exige admin consent, así que cada usuario los acepta
   en su propio primer login, sin pedir nada a ITESO IT.
4. Probar el login con una cuenta `@iteso.mx`: debería aparecer la pantalla
   normal de "Este sitio requiere tu permiso para..." con **Aceptar**, no el
   mensaje de "You don't have access to this" (ese es específico del portal
   admin, no de este flujo).
5. Dos IDs distintos, no lo mezcles: el **MSAL del SPA** apunta al endpoint
   `/common/` o `/organizations/` (no al de tu tenant, porque la app es
   multi-tenant) con `client_id` = el Application ID de tu registro. El
   **backend** (`MICROSOFT_CLIENT_ID`) usa ese mismo `client_id`, pero
   `MICROSOFT_TENANT_ID` sigue siendo el tenant de **ITESO**
   (`6f0348f2-e498-45c9-84f4-c6d81dcffdfe`), porque `EntraProvider` valida el
   `tid` del `id_token`, y ese `tid` es el tenant del usuario que inició
   sesión (ITESO), no el tenant donde registraste la app.

Si ITESO también bloquea el consentimiento individual a apps externas
(“user consent to apps from other tenants”), este paso fallará con un error
de aprobación de administrador distinto al de arriba, y en ese caso sí hace
falta IT. Vale la pena intentarlo, porque es un mecanismo distinto al que
bloqueó el portal.

### Si en algún momento hay acceso al tenant de ITESO

Registrar la app dentro del tenant de ITESO (single-tenant) es preferible a
largo plazo. Los mismos permisos (`openid`, `profile`, `email`,
`offline_access`, `User.Read`) no requieren admin consent ahí tampoco.

**`GroupMember.Read.All` queda fuera de cualquiera de las dos rutas.** Es el
único permiso de esta lista que exige "admin consent", y pedirlo junto con
el resto puede hacer que Entra rechace el login completo si no fue
aprobado. Sin él se pierden las membresías de grupo
(`UserProfile.microsoft_group_ids` queda vacío), pero el resto de los datos
de Graph (nombre, `jobTitle`, `department`, `employeeId`) se sigue leyendo
con `User.Read`, porque es el propio usuario leyendo su registro. Si más
adelante T2-13 necesita el grupo para distinguir STAFF de STUDENT, ese es el
momento de pedirle a IT que apruebe `GroupMember.Read.All`.

## Desarrollo local sin un registro real

Con `MICROSOFT_AUTH_MODE=mock` (valor por defecto en `.env.example`), el
backend acepta un `id_token` con el prefijo `mock:`:

```sh
# Email simple
curl -X POST localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"id_token": "mock:ana@iteso.mx", "access_token": ""}'

# Con campos adicionales
curl -X POST localhost:8000/api/v1/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"id_token": "mock:{\"email\":\"ana@iteso.mx\",\"department\":\"Ingeniería de Software\"}", "access_token": ""}'
```

El `access_token` se ignora en modo mock. `MICROSOFT_AUTH_MODE=mock` se
rechaza al arrancar Django si `DJANGO_ENV` no es `dev` o si
`DJANGO_DEPLOYED=true`.

## Contrato para el frontend (T2-12)

```jsonc
// Request
{ "id_token": "<MSAL id_token>", "access_token": "<MSAL Graph access_token>" }

// Response 200
{
  "access": "<jwt>",
  "refresh": "<jwt>",
  "user": { "id": "<uuid>", "email": "ana@iteso.mx", "first_name": "Ana", "last_name": "García", "role": "STUDENT" },
  "created": true
}

// Errores: {"error": {"code": "...", "message": "..."}}
// 400 VALIDATION_ERROR · 401 UNAUTHENTICATED · 403 DOMAIN_NOT_ALLOWED / PERMISSION_DENIED · 429 (throttle)
```

`created=true` en el primer login habilita a T2-12/T2-30 a arrancar el flujo
de onboarding (selección de carrera, clan institucional).

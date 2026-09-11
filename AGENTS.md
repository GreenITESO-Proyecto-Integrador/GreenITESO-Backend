# Reglas del repositorio para agentes y colaboradores

Las decisiones de este proyecto tienen prioridad sobre las sugerencias
generales de las skills vendorizadas:

- Desarrollo local y CI usan PostgreSQL 18; no requieren cuenta, login, CLI ni
  credenciales de Neon.
- Neon solo tiene las ramas cloud dev, staging y production. No se crean
  ramas por PR ni se usa Neon para bases de cada desarrollador.
- Todo comando identifica --project-id; las operaciones sobre una rama
  identifican también su destino explícito (nombre/ID o --branch según el comando). No se hace neon env pull durante el onboarding; cuando
  corresponda, se usa --no-env-pull.
- Los modelos y migraciones de Django son la única fuente del esquema. No se
  agrega neon.ts ni se usa Neon Auth: la autenticación acordada es Firebase y
  GCS privado sigue siendo la propuesta P1 pendiente de integración.
- Una rama de Git no mezcla ni promueve datos. Las migraciones se revisan y se
  ejecutan una vez por ambiente con la conexión directa; la aplicación usa la
  URL pooled.

P3/P8/P9/P10/P11 siguen pendientes de ratificación. Se permiten implementaciones en borradores identificados; no se fusionan ni se
aplican a ambientes compartidos hasta registrar la ratificación.

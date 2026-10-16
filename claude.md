# Plan del proyecto: MCP para Odoo 19

## Objetivo

Crear en esta carpeta un servidor MCP independiente para que clientes de IA y n8n consulten y gestionen tareas de proyectos y páginas web en una o varias instancias propias de Odoo 19 Community.

El servicio expondrá un endpoint remoto común mediante HTTPS y MCP Streamable HTTP. Se ejecutará como servicio independiente, preferiblemente en Docker, y se conectará a Odoo mediante una API compatible con la instalación. No se ofrecerá acceso genérico a la base de datos ni ejecución arbitraria de SQL.

## Alcance funcional inicial

### Proyectos y tareas

- Buscar y consultar proyectos.
- Buscar y consultar tareas.
- Crear tareas.
- Actualizar tareas.

### Páginas web

- Buscar y consultar páginas.
- Editar páginas.
- Publicar una página mediante una operación explícita.
- Despublicar una página mediante otra operación explícita.

Las herramientas MCP tendrán nombres y parámetros específicos. Cada herramienta validará el destino Odoo, el modelo, los campos y los valores admitidos.

## Principios de diseño y seguridad

- El cliente debe seleccionar explícitamente la instancia Odoo de destino; no se inferirá por defecto.
- Cada instancia tendrá configuración separada.
- Se usarán credenciales de un usuario técnico de Odoo con los permisos mínimos necesarios.
- Las credenciales no se guardarán en el código ni en el repositorio; se suministrarán mediante secretos o variables de entorno.
- El endpoint MCP remoto requerirá autenticación y HTTPS.
- Cada usuario cliente tendrá un token propio y uno o más roles limitados a herramientas concretas; las reglas de acceso de Odoo se mantienen como segunda capa.
- Las operaciones de publicación y despublicación serán distintas de la edición de contenido.
- Se registrarán las operaciones y los errores sin exponer secretos.
- No se habilitarán consultas SQL arbitrarias ni acceso genérico a modelos o campos.

## Fases de trabajo

### 1. Inventario y comprobación de compatibilidad

- Identificar cada instancia Odoo que se integrará.
- Registrar versión exacta, edición, alojamiento y entorno (pruebas o producción).
- Comprobar qué API ofrece cada instalación de Odoo 19 Community y si permite realizar con precisión las operaciones previstas.
- Verificar las capacidades disponibles para autenticación, lectura, escritura y publicación de páginas.
- Decidir si basta la API existente o si se necesita un módulo Odoo dedicado.

**Resultado:** matriz de instancias y decisión documentada sobre la integración.

### 2. Contrato de herramientas y permisos

- Definir los campos consultables y modificables de proyectos, tareas y páginas.
- Definir filtros de búsqueda, límites, ordenación y formato de respuesta.
- Establecer reglas para creación y actualización de tareas.
- Establecer reglas de edición, publicación y despublicación de páginas.
- Definir los permisos mínimos del usuario técnico y cómo se selecciona el destino.

**Resultado:** contrato de herramientas MCP y matriz de permisos aprobados para implementar.

### 3. Servidor MCP

- Elegir el SDK y la estructura del servicio según las capacidades confirmadas de los clientes.
- Implementar Streamable HTTP y autenticación para clientes remotos.
- Implementar configuración explícita para una o varias instancias Odoo.
- Añadir validación estricta de entradas, errores claros y registro seguro de operaciones.
- Implementar las herramientas de lectura y escritura definidas en la fase 2.

**Resultado:** servicio ejecutable localmente y preparado para empaquetarse como contenedor.

### 4. Módulo Odoo dedicado (si hace falta)

- Crear un módulo limitado a las operaciones y permisos requeridos si la API disponible no ofrece control suficiente.
- Evitar exponer métodos genéricos o acceso amplio a modelos.
- Documentar instalación, configuración y actualización del módulo.

**Resultado:** integración Odoo acotada y documentada, solo si la fase 1 la requiere.

### 5. Despliegue y protección

- Crear la imagen y configuración de Docker.
- Configurar HTTPS, autenticación, secretos, persistencia necesaria y política de registros.
- Documentar variables de entorno y despliegue para cada instancia.
- Asegurar que los secretos no aparezcan en logs ni en respuestas MCP.

**Resultado:** servicio desplegable como servicio independiente.

### 6. Conexión de clientes

- Confirmar el transporte MCP y el método de autenticación admitidos por cada cliente de IA objetivo.
- Conectar un cliente de IA remoto mediante Streamable HTTP.
- Conectar n8n usando su soporte MCP si está disponible en la versión desplegada; en caso contrario, usar llamadas HTTP al mismo servicio.
- Añadir otros clientes después de validar los primeros.

**Resultado:** instrucciones de conexión verificadas para cada cliente elegido.

### 7. Validación y paso a producción

- Validar primero en una instancia de prueba.
- Comprobar lecturas, creación y actualización de tareas, edición de páginas y publicación/despublicación.
- Comprobar autenticación, selección explícita de instancia, límites de permisos y tratamiento de errores.
- Revisar registros y confirmar que no contienen credenciales.
- Pasar a producción solo después de completar las comprobaciones acordadas.

**Resultado:** lista de validación completada y procedimiento de despliegue a producción.

## Decisiones pendientes

- Instancias Odoo objetivo y datos de versión exacta, alojamiento y entorno.
- Clientes de IA y versiones de n8n que se conectarán primero.
- Campos y reglas de negocio concretos para tareas y páginas.
- API disponible en cada instalación y necesidad de un módulo dedicado.
- Método de autenticación esperado por los clientes MCP y política de HTTPS.
- Entorno de despliegue, gestión de secretos y política de retención de registros.

## Próximo paso

La instancia de prueba indicada es `https://odoo.example.com`, con Project y Website activos. La comprobación de solo lectura a `/web/version` respondió `19.0-20260926`; el entorno es accesible desde el contenedor.

Se ha creado y ejecutado una versión inicial del servidor MCP en Docker. Implementa Streamable HTTP y usa JSON-2. La suite inicial tuvo 9 pruebas aprobadas, incluida la comprobación de versión en vivo. Se añadió autenticación Bearer y permisos por roles. El servicio tiene una página local de administración (`/admin`) para crear usuarios, asignar roles, rotar tokens y revocar acceso; los cambios se guardan en un volumen SQLite y aplican de inmediato. El token inicial de `owner` se configura en `.env`; los tokens de nuevos usuarios se muestran una sola vez y solo se guardan como hash. Esta primera versión usa tokens estáticos; todavía no implementa OAuth ni HTTPS remoto.

Se añadieron herramientas MCP para crear y actualizar proyectos, con campos limitados a nombre, descripción, estado activo, visibilidad, responsable, cliente y fechas. Se creó el rol `project_manager` para esas operaciones y lectura de proyectos.

La instancia de prueba indicó inicialmente que el usuario técnico no podía crear proyectos; por eso sigue pendiente crear el proyecto y validar escrituras en Odoo. El MCP tampoco expone todavía una herramienta `create_project`.

Siguiente: conectar un cliente con el token del rol que corresponda, añadir identidades individuales según necesidad, y probar permisos de lectura/escritura con datos temporales en la instancia de prueba. Implementar OAuth y HTTPS al preparar acceso remoto.

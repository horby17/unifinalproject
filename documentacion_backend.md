# Documentación técnica del backend

## 1. Propósito del backend dentro del sistema

El backend del sistema tiene la responsabilidad de coordinar la lógica principal de la aplicación web orientada a la evaluación técnica de levantamientos de powerlifting. Su función central es servir las vistas de la interfaz, autenticar usuarios, registrar atletas, procesar intentos, analizar postura corporal mediante visión por computadora y persistir la información generada en la base de datos.

En términos funcionales, este segmento actúa como capa de integración entre cuatro elementos principales:

- la interfaz web construida con plantillas HTML;
- la lógica de negocio del sistema;
- el procesamiento de imágenes y video con detección de pose;
- el almacenamiento estructurado de usuarios, atletas, intentos y eventos de levantamiento.

Por tanto, el backend no solo expone rutas HTTP, sino que también ejecuta reglas de evaluación técnica y administra la comunicación entre los módulos de análisis y la persistencia de datos.

## 2. Tecnologías utilizadas en este segmento

Las tecnologías identificadas en el backend real del proyecto son las siguientes:

- **Python** como lenguaje principal del servidor.
- **Flask** como microframework web para definir rutas, sesiones, respuestas HTML, respuestas JSON y streaming de video.
- **Jinja2** para el renderizado de plantillas HTML desde el servidor.
- **SQLite** como motor de base de datos local, almacenado en `data/app.db`.
- **SQLAlchemy** para el mapeo ORM de las entidades `Athlete` y `Attempt`, así como para la gestión de sesiones de base de datos en varias rutas.
- **sqlite3** de Python para operaciones directas sobre tablas como `users` y `lift_events`, y para ajustes de esquema.
- **Werkzeug** para el hash y verificación de contraseñas, además del saneamiento de nombres de archivos cargados.
- **OpenCV (cv2)** para captura, lectura, escritura y manipulación de fotogramas e imágenes.
- **MediaPipe Tasks / Pose Landmarker** para la detección de puntos anatómicos y el cálculo de métricas corporales.
- **NumPy** para estructuras auxiliares en imágenes, por ejemplo en la generación de fotogramas de respaldo.
- **threading** para proteger con bloqueos la inicialización del modelo de pose y el estado compartido del último resultado detectado.

## 3. Archivos principales relacionados con el backend

El backend está concentrado principalmente en los siguientes archivos:

- `main.py`: archivo central del servidor Flask. Contiene configuración global, modelos ORM, inicialización de base de datos, autenticación, rutas web, endpoints JSON, streaming de cámara y análisis de intentos.
- `deadlift_rules.py`: implementa una máquina de estados para evaluar la ejecución del peso muerto en tiempo real.
- `bench_rules.py`: implementa una máquina de estados para evaluar la secuencia técnica del press de banca en tiempo real.
- `powerlifting_rules/exercise_rules.py`: módulo auxiliar con lógica orientada a sentadilla; existe en el proyecto, pero no se encuentra integrado al flujo principal de `main.py`.
- `powerlifting_rules/pose_utils.py`: utilidades de detección de pose y cálculo de ángulos; también existe como módulo auxiliar, pero actualmente no es utilizado por el backend principal.
- `requirements.txt`: lista de dependencias del entorno backend.
- `data/app.db`: base de datos SQLite utilizada por el sistema.
- `diagrama.er`: diagrama entidad-relación del proyecto, útil como referencia documental del diseño de datos.

## 4. Funcionalidades implementadas desde el backend

Con base en el código actual, el backend implementa las siguientes funciones reales:

- autenticación de usuarios mediante inicio de sesión, registro y cierre de sesión;
- control de acceso simple mediante sesión almacenada en Flask;
- creación de un usuario administrador inicial (`admin`) cuando la tabla `users` está vacía;
- registro de atletas mediante formulario web y validación de campos;
- consulta de atletas para poblar la interfaz de captura;
- visualización del tablero principal con los intentos recientes;
- carga y almacenamiento de videos enviados al servidor;
- análisis automático de videos almacenados mediante detección de pose;
- cálculo de ángulos corporales relevantes, especialmente rodilla, cadera y codo;
- evaluación simplificada de intentos para `Squat`, `Bench` y `Deadlift` a partir de umbrales angulares;
- transmisión en tiempo real de la cámara con superposición visual de puntos corporales y métricas;
- evaluación en tiempo real específica para `Bench` y `Deadlift` mediante máquinas de estados;
- almacenamiento de evidencia gráfica de ciertos eventos detectados en tiempo real;
- registro de eventos de levantamiento en base de datos;
- exposición de endpoints JSON para consultar el último resultado de pose y su evaluación.

## 5. Manejo de rutas, lógica del sistema y conexión con otros módulos

El backend define las rutas HTTP directamente en `main.py`. No se observa una separación por controladores, blueprints ni servicios independientes; el sistema sigue una estructura monolítica donde las rutas, la lógica y parte del acceso a datos coexisten en el mismo archivo principal.

### Rutas web principales

- `/`: página principal del panel de cámara. Si no existe sesión, redirige a `/login`.
- `/login`: autenticación de usuarios.
- `/register`: creación de nuevos usuarios.
- `/logout`: destrucción de la sesión actual.
- `/dashboard`: muestra información general y los intentos recientes.
- `/settings`: vista estática de configuración.
- `/athletes/new`: formulario para registrar nuevos atletas.

### Rutas orientadas a análisis y captura

- `/attempts/auto`: endpoint `POST` que recibe un video, valida datos del atleta y tipo de levantamiento, ejecuta el análisis y guarda el intento.
- `/pose/stream`: devuelve una respuesta tipo `multipart/x-mixed-replace` para transmitir video procesado en tiempo real.
- `/video_feed`: expone el mismo flujo de streaming que `/pose/stream`.
- `/api/pose/latest`: devuelve en JSON el último estado de pose detectado.
- `/api/pose/evaluate`: evalúa el último estado detectado para `Squat`, `Bench` o `Deadlift`.

### Lógica del sistema

La lógica del sistema se ejecuta principalmente en tres niveles:

- **nivel de acceso**: verificación de sesión con `require_login()`;
- **nivel de negocio**: validación de formularios, evaluación de postura y reglas por ejercicio;
- **nivel de integración**: guardado de archivos, persistencia en base de datos y respuesta a la interfaz.

### Conexión con otros módulos

El archivo `main.py` importa y utiliza de forma activa:

- `DeadliftState` y `evaluate_deadlift` desde `deadlift_rules.py`;
- `BenchStateMachine` desde `bench_rules.py`.

En cambio, los archivos bajo `powerlifting_rules/` no están conectados actualmente al flujo principal del servidor. Esto indica que forman parte de una implementación auxiliar, experimental o anterior, pero no del backend efectivamente en ejecución según el código principal.

## 6. Relación del backend con la base de datos

La relación con la base de datos combina dos enfoques:

- **ORM con SQLAlchemy** para las tablas `athletes` y `attempts`;
- **acceso directo con sqlite3** para `users`, `lift_events` y operaciones de migración ligera.

### Tablas verificadas en la base de datos real

En `data/app.db` existen actualmente las tablas:

- `users`
- `athletes`
- `attempts`
- `lift_events`
- `athlete_audit`

### Uso real de cada tabla desde el backend

- `users`: utilizada para autenticación y registro de usuarios.
- `athletes`: utilizada para registrar y consultar atletas.
- `attempts`: utilizada para almacenar intentos analizados.
- `lift_events`: utilizada para registrar eventos técnicos detectados en tiempo real, especialmente en banca y peso muerto.
- `athlete_audit`: existe en la base de datos y en el diagrama, pero no se encontró lógica activa en `main.py` que la utilice.

### Consideraciones técnicas observadas

- La inicialización de la base de datos se realiza en `init_db()`.
- El método `Base.metadata.create_all(ENGINE)` crea tablas ORM, pero el sistema también ejecuta sentencias SQL manuales para completar otras estructuras.
- Existe la función `ensure_attempts_schema()` para ajustar la tabla `attempts` si el esquema previo tenía restricciones incompatibles con el campo `weight`.
- El proyecto presenta una mezcla de modelo ORM y SQL manual, lo cual funciona, pero incrementa el acoplamiento técnico y el riesgo de divergencias entre código, diagrama y esquema real.

## 7. Relación del backend con la interfaz web

La interfaz web depende directamente del backend para renderizado, suministro de datos y evaluación técnica.

### Renderizado del lado del servidor

El backend entrega plantillas HTML mediante `render_template()`. Entre las principales plantillas conectadas al backend se encuentran:

- `templates/login.html`
- `templates/register.html`
- `templates/dashboard.html`
- `templates/settings.html`
- `templates/athlete_create.html`
- `templates/camera.html`

Estas vistas reciben datos desde Flask, por ejemplo:

- nombre del usuario en sesión;
- listado de atletas;
- historial reciente de intentos;
- mensajes de error o confirmación;
- estado de navegación de la página.

### Integración con JavaScript del cliente

La pantalla principal de captura usa `static/js/app.js`, que interactúa con el backend en dos formas:

- consume el stream en tiempo real desde `/pose/stream`;
- consulta el resultado actual con `/api/pose/evaluate`.

Esto permite que la interfaz muestre el video procesado y un resultado inmediato de evaluación sin recargar la página.

### Observación importante sobre el flujo actual

Aunque el backend sí implementa el endpoint `/attempts/auto` para recibir videos y guardar intentos completos, el archivo `static/js/app.js` actualmente no envía videos a esa ruta. En la interfaz analizada, el botón de captura solo toma el frame visual mostrado en pantalla y consulta la evaluación JSON del último estado de pose. Por tanto, existe una diferencia entre la capacidad implementada en el backend y el flujo efectivamente conectado en el frontend actual.

## 8. Observaciones técnicas importantes

Del análisis del proyecto real se desprenden las siguientes observaciones:

- El backend está desarrollado como una aplicación monolítica en `main.py`. Esto simplifica el arranque del sistema, pero concentra demasiadas responsabilidades en un solo archivo.
- La autenticación es funcional, pero básica. No se observan roles, permisos avanzados ni políticas de seguridad adicionales.
- La clave secreta de Flask usa una variable de entorno (`FLASK_SECRET`), pero también posee un valor por defecto de desarrollo (`dev-secret-change-me`), lo que no es recomendable para producción.
- El proyecto mezcla SQLAlchemy y `sqlite3` directo. Técnicamente es válido, pero conviene unificar el acceso a datos para mantener consistencia.
- Existen diferencias entre el diagrama ER, la base de datos real y los modelos ORM. Por ejemplo, en la base se observan columnas como `created_at`, mientras que los modelos ORM no siempre las representan.
- La tabla `athlete_audit` existe en la base de datos, pero no se encontró funcionalidad activa que escriba o consulte esa información desde `main.py`.
- Los módulos `powerlifting_rules/exercise_rules.py` y `powerlifting_rules/pose_utils.py` existen, pero no forman parte del flujo principal del backend actual. Deben considerarse componentes no integrados o pendientes de incorporación.
- La evaluación de `Squat` en tiempo real se resuelve con una lógica angular simple en `main.py`, mientras que `Bench` y `Deadlift` sí poseen máquinas de estados especializadas.
- La función `realtime_pose_stream()` también asume múltiples responsabilidades: captura de cámara, inferencia de pose, decisión técnica, persistencia de eventos y generación de frames para streaming.
- El sistema depende de la disponibilidad de un modelo `.task` de MediaPipe en la carpeta `models/` y de dispositivos de cámara accesibles en rutas como `/dev/video0`, `/dev/video1`, `/dev/video2` o `/dev/video3`.
- El proyecto sí guarda evidencia visual en disco, tanto en `uploads/attempts/` como en `frames/`, lo cual complementa la trazabilidad del análisis técnico.

## Conclusión técnica

El backend del sistema cumple el papel de núcleo operativo de la plataforma. Administra usuarios, atletas, intentos y eventos técnicos, además de integrar visión por computadora para apoyar la evaluación de levantamientos. Su implementación actual es funcional y reúne en una sola aplicación Flask los procesos de interfaz, lógica y persistencia.

No obstante, el análisis del proyecto muestra que todavía existen componentes parcialmente integrados, diferencias entre diseño y ejecución real, y oportunidades claras de refactorización para separar responsabilidades, fortalecer la consistencia del modelo de datos y conectar mejor ciertos endpoints ya implementados con la interfaz web activa.

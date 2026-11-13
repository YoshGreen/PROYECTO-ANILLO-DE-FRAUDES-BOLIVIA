# Sistema de Detección de Anillos de Fraude y Cuentas Fantasma en Billeteras Móviles (Yape Bolivia)

**Materia:** Base de Datos III
**Universidad:** Universidad Mayor de San Andrés (UMSA)
**Dupla No. 2:** Yoshua Cordova & Erick Serpemtegui (2026)
**Arquitectura:** NoSQL Políglota 100 % Local (MongoDB + Redis)

---

## 1. Descripción del Proyecto

Aplicación web desarrollada en **Flask** que implementa integralmente el diseño de dominio (DDD) y los servicios de evaluación de riesgo del **Informe de Caso de Estudio No. 2**:

* **MongoDB 8.3 local (`localhost:27017` / base `yape_fraude`):**
  * Almacenamiento inmutable de transacciones con `WriteConcern("majority")`.
  * Índices *multikey* en arreglos `dispositivos` y `telefonos` para detectar identidades compartidas.
  * Proyección precalculada `aristas_flujo` (patrón *Computed*) para rastrear grafos de dinero.
  * Recorridos recursivos en grafos con etapas `$graphLookup` para detectar ciclos de transferencias circulares (anillos de fraude) y trazabilidad de fondos.
* **Redis local (`localhost:6379`):**
  * Validación en tiempo real con complejidad $O(1)$ y latencia submilisegundo ($<50$ ms).
  * Conjuntos (*Sets*): `blacklist:users`, `blacklist:devices`, `blacklist:phones`.
  * Contadores volátiles de ráfaga (*Velocity Check*): `velocity:tx:<id_usuario>` con TTL de 60 segundos.
  * Caché de puntaje de riesgo con TTL de 300 segundos: `risk:score:<id_usuario>`.

---

## 2. Escala Ligera Optimizada para Laptop

Para evitar sobrecargar la memoria y disco de tu equipo local, el generador sintético produce una muestra compacta y representativa:

* **300 usuarios** y **1,500 transacciones** (se generan en solo **2.3 segundos** y consumen menos de 10 MB de RAM).
* Incluye con precisión los **5 anillos de fraude sembrados** del informe:
  * Anillos circulares de 3, 3, 4, 4 y 5 cuentas (19 cuentas en total).
  * Dispositivos compartidos (`DEV-SHARED-00` a `DEV-SHARED-04`).
  * Líneas secundarias compartidas (`+59169000000` a `+59169000004`).
  * Cuentas bloqueadas precargadas en Redis.

---

## 3. Estructura de Archivos

```
Web_AnillosFraude/
├── app.py                  # Servidor Flask principal y endpoints API REST
├── config.py               # Configuración de conexiones locales (MongoDB y Redis)
├── servicios.py            # Servicios de Dominio DDD (Evaluador, Detector, Gestor)
├── generar_datos.py        # Generador de datos sintéticos y validadores de esquema
├── requirements.txt        # Dependencias de Python (Flask, pymongo, redis)
├── iniciar_servidor.bat    # Lanzador de un solo clic para Windows
├── templates/
│   ├── base.html           # Plantilla base con navbar y badges de estado
│   ├── index.html          # Dashboard general con métricas en tiempo real
│   ├── transferir.html     # Simulador de pagos y validación antifraude
│   ├── anillos.html        # Detector de anillos con visualizador de grafos Vis.js
│   ├── cuentas_fantasma.html # Auditoría de celulares y teléfonos compartidos
│   ├── alertas.html        # Bandeja de alertas y bloqueo en cascada
│   ├── listas_negras.html  # Inspección de claves y TTLs en memoria de Redis
│   ├── trazabilidad.html   # Ruta del dinero ($graphLookup)
│   └── compass_info.html   # Guía paso a paso para MongoDB Compass
└── static/
    ├── css/style.css       # Estilos temáticos Yape Bolivia (púrpura y cian)
    └── js/main.js          # Controladores AJAX y visualización interactiva
```

ca

---

## 4. Cómo Ejecutar el Proyecto

### Opción A (Doble clic):

Ejecuta el archivo `iniciar_servidor.bat`.

### Opción B (Desde terminal):

```powershell
cd Web_AnillosFraude
& python app.py
```

Luego abre en tu navegador:
👉 **`http://127.0.0.1:5000`**

---

## 5. Inspección en MongoDB Compass

1. Abre **MongoDB Compass**.
2. Conéctate a: `mongodb://localhost:27017`
3. Abre la base de datos `yape_fraude`.
4. Podrás ver y consultar en tiempo real las 5 colecciones:
   * `usuarios`
   * `transacciones`
   * `aristas_flujo`
   * `alertas_fraude`
   * `reportes_estafa`

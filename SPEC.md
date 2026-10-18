# **Contexto y Especificaciones: Sistema de Trading Cuantitativo Agéntico**

# **Documento de Transición para Google Antigravity (Modelo Argon)**

Este documento contiene la arquitectura completa, especificaciones de infraestructura, estado de desarrollo y la hoja de ruta detallada para la transición del motor de trading cuantitativo agéntico hacia una interfaz gráfica integral (GUI) desarrollada en el entorno Google Antigravity con el modelo Argon.

---

# **1\. Visión General del Proyecto**

El **Sistema de Trading Cuantitativo Agéntico** es una solución de inversión automatizada diseñada para combinar la precisión matemática del análisis cuantitativo con la capacidad cualitativa de agentes de inteligencia artificial.

## **Objetivos Principales**

* **Escaneo Cuantitativo Automatizado:** Identificación continua de oportunidades de mercado mediante indicadores técnicos rigurosos.  
* **Gestión Estricta del Riesgo:** Limitación estricta de la exposición máxima por operación al 1% del valor total de la cartera y dimensionamiento dinámico de la posición.  
* **Análisis Cualitativo Agéntico:** Generación de tesis de inversión ejecutivas y evaluación sintética de contexto antes de la aprobación.  
* **Control Humano (Human-in-the-Loop):** Flujo de trabajo interactivo donde las decisiones de compra requieren validación explicita previa a la ejecución.  
* **Ejecución Automatizada Bracket (OCO):** Enrutamiento directo de órdenes de compra con límites de pérdidas (Stop Loss) y toma de ganancias (Take Profit) en el bróker (Alpaca).  
* **Migración a GUI Integral:** Construcción de un panel web unificado de orquestación, supervisión y gestión operado a través de Google Antigravity usando Argon.

---

# **2\. Arquitectura Técnica Actual**

La arquitectura actual opera sobre una infraestructura en contenedores Docker, con comunicación asíncrona mediante webhooks y servicios expuestos a través de un túnel seguro.

## **2.1 Orquestación e Infraestructura (Docker Compose)**

* **`n8n`:** Plataforma de orquestación de flujos de trabajo. Realiza las siguientes funciones:  
  * Escuchar webhooks y eventos programados.  
  * Agrupar y procesar señales enviadas por el motor de Python.  
  * Intercambiar datos con el modelo Google Gemini para generación de análisis.  
  * Gestionar la interfaz conversacional e interactiva mediante bot de Telegram.  
* **`quant-engine`:** Motor analítico y ejecutor desarrollado en Python 3.11, expuesto mediante FastAPI y ejecutado bajo el servidor Uvicorn en el puerto `8000`.

## **2.2 Motor de Trading en Python (`quant-engine`)**

El motor ejecuta los algoritmos cuantitativos y las conexiones con librerías financieras (`yfinance`, `ta`, `alpaca-py`):

| Componente | Configuración / Algoritmo |
| :---- | :---- |
| **Indicadores Técnicos** | Oscilador Estocástico (14, 4)ADX (14)Media Móvil Simple SMA (200)Average True Range ATR (14) |
| **Regímenes de Mercado** | **Tendencial:** Precio \> SMA(200) **Y** ADX \> 25**Lateral:** ADX \< 20 |
| **Riesgo Máximo** | \$1.0%\$ del equity total de la cuenta por operación |
| **Stop Loss (SL)** | \$1.5 \\times \\text{ATR}(14)\$ por debajo del precio de entrada |
| **Take Profit (TP)** | \$2.0 \\times \\text{Distancia al Stop Loss}\$ (Ratio Riesgo/Beneficio \$1:2\$) |
| **Filtro de Comisiones** | \$\\text{Comisiones (Ida \+ Vuelta)} \\le 10% \\times \\text{Beneficio Bruto Esperado}\$ |

### **Endpoints Expuestos por FastAPI**

* `GET /health`: Comprobación de disponibilidad de la API y de los servicios subyacentes.  
* `POST /scan-market`: Recibe una lista de tickers, calcula indicadores y devuelve oportunidades viables filtradas.  
* `POST /validate-risk`: Evalúa de forma aislada una propuesta de operación, calculando el tamaño exacto de la posición, precios de SL y TP.  
* `POST /execute-bracket`: Transmite la orden OCO (Límite \+ SL \+ TP) a Alpaca Paper Trading (o ejecuta simulaciones en seco).

## **2.3 Agente de IA e Interacción**

* **Motor Cognitivo:** Google Gemini Chat Model implementado en n8n para la generación de resúmenes ejecutivos del setup técnico en lenguaje claro.  
* **Interfaz Operativa Actual:** Bot de Telegram interactivo configurado con nodos `Send and Wait for Response`, presentando botones interactivos `[✅ Aprobar Compra]` y `[❌ Rechazar]`.  
* **Exposición Externa:** Túnel de Cloudflare (`cloudflared`) que garantiza conexión HTTPS segura para la recepción de webhooks de Telegram en entornos de desarrollo local.

---

# **3\. Estado de Avance (Hitos Completados)**

El proyecto ha completado su fase inicial de prototipo funcional del backend y la orquestación:

- [x] **Contenedorización en Docker:** Configuración de `docker-compose.yml` unificando el motor FastAPI y n8n con redes internas aisladas.  
- [x] **Motor Cuantitativo Operativo:** Métricas de ATR, ADX, SMA200 y lógica de sizing probadas con datos reales de `yfinance`.  
- [x] **Flujo Integrado en n8n:**  
      1. Trigger programado o manual.  
      2. Petición HTTP a `/scan-market`.  
      3. Descomposición con nodo `Split Out`.  
      4. Evaluación cualitativa mediante el nodo de Gemini AI Agent.  
      5. Envío de mensaje interactivo por Telegram (`Send and Wait`).  
      6. Evaluación de la respuesta mediante nodo `If`.  
      7. Ejecución de la orden mediante `POST /execute-bracket`.  
      8. Notificación final de confirmación de orden ejecutada.  
- [x] **Resolución de Red:** Implementación del túnel de Cloudflare resolviendo incompatibilidades de SSL/TLS (errores 400 y ERR\_NGROK\_3200 de Telegram).  
- [x] **Normalización de Payloads:** Corrección en el formateo del JSON enviado desde n8n al motor Python para garantizar la serialización de tipos flotantes y cadenas.

---

# **4\. Trabajo Pendiente y Roadmap para la GUI**

El desarrollo se centra ahora en reemplazar las iteraciones manuales y las interfaces secundarias (Telegram) por un centro de control visual unificado.┌─────────────────────────────────────────────────────────────────┐

│                    GUI INTERACTIVA (FRONTEND)                   │

│  ┌───────────────────┐  ┌──────────────────┐  ┌──────────────┐  │

│  │ Metrics Dashboard │  │  Live Charting   │  │ Approval Hub │  │

│  └─────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘  │

└────────────┼─────────────────────┼───────────────────┼──────────┘

             │                     │                   │

             ▼                     ▼                   ▼

┌─────────────────────────────────────────────────────────────────┐

│                       BACKEND & FASTAPI                         │

│  ┌───────────────────┐  ┌──────────────────┐  ┌──────────────┐  │

│  │  /scan-market     │  │  /validate-risk  │  │ /execute-... │  │

│  └─────────┬─────────┘  └────────┬─────────┘  └──────┬───────┘  │

└────────────┼─────────────────────┼───────────────────┼──────────┘

             │                     │                   │

             ▼                     ▼                   ▼

┌─────────────────────────────────────────────────────────────────┐

│                     PERSISTENCIA Y EXECUCIÓN                    │

│  ┌─────────────────────────────────┐  ┌──────────────────────┐  │

│  │   PostgreSQL / Supabase DB      │  │  Alpaca Paper API    │  │

│  └─────────────────────────────────┘  └──────────────────────┘  │

└─────────────────────────────────────────────────────────────────┘

1. ## **Roadmap Detallado por Fases**

2. **Fase 1 (Prioritaria / Inmediata) — Motor Autónomo e Integración de Paper Trading:**  
   * **Bucle y Automatización del Escaneo:** Implementar un worker / scheduler de escaneo continuo con control manual de arranque/parada (start/stop) y barridos periódicos rápidos (ej. cada 15 segundos o configurable) para la detección inmediata de oportunidades.  
   * **Integración y Validación en Vivo (Alpaca Paper Trading):** Cargar credenciales definitivas en `.env`, desactivar la bandera `force_test_signal: true` y validar la transmisión de órdenes reales OCO, consulta de balances y ejecuciones simuladas en vivo para confirmar la validez total de la herramienta.  
3. **Fase 2 (Posterior) — Desarrollo del Frontend GUI (Antigravity \+ Argon):**  
   * **Dashboard Principal:** Visualización del estado de la cartera, margen disponible, exposición total y PnL diario.  
   * **Gráficos e Indicadores:** Gráficos de velas interactivos con capas superpuestas de SMA(200), niveles de ATR, y marcadores de entrada/salida.  
   * **Panel de Aprobación Human-in-the-Loop:** Interfaz gráfica nativa para revisar la tesis generada por Gemini, modificar parámetros de riesgo (si se requiere) y aprobar/descartar la orden con un solo clic.  
   * **Historial e Ingesta:** Listado de órdenes ejecutadas, estados de órdenes OCO activas en Alpaca y registro de auditoría.  
4. **Persistencia de Datos:**  
   * Integración con PostgreSQL / Supabase para mantener un registro histórico de análisis agénticos, señales rechazadas y rendimiento de la estrategia.

---

# **5\. Instrucciones de Transición a Google Antigravity (Argon)**

Siga las siguientes instrucciones para iniciar el desarrollo de la interfaz gráfica dentro del entorno Google Antigravity utilizando el modelo Argon.

## **5.1 Apertura del Espacio de Trabajo**

1. Abra su terminal o interfaz de línea de comandos.  
2. Inicie el entorno Google Antigravity apuntando a la ruta raíz del proyecto:antigravity open C:\\proyectos\\trading-agent-system  
3. Verifique que los contenedores actuales continúen ejecutándose en segundo plano (`docker compose ps`).

## **5.2 Stack Recomendado para la GUI**

Para mantener la congruencia con la arquitectura existente, se recomienda seleccionar el siguiente stack tecnológico en Antigravity:

* **Framework:** Next.js (App Router) o Vite \+ React (TypeScript).  
* **Estilos:** Tailwind CSS con componentes shadcn/ui para una estética financiera minimalista y clara.  
* **Visualización de Gráficos:** Lightweight Charts (TradingView) o Recharts.  
* **Gestión de Estado y API:** TanStack Query (React Query) para sincronización con los endpoints de FastAPI.

## **5.3 Prompt de Inicio para Inyectar a Argon**

Copie y pegue el siguiente prompt dentro de la interfaz de Antigravity al iniciar la sesión con el modelo Argon:Hola Argon. Asume el rol de Senior Quantitative Trading Developer & System Architect. 

Estamos trabajando en el proyecto ubicado en "C:\\proyectos\\trading-agent-system". Este es un Sistema de Trading Cuantitativo Agéntico que actualmente cuenta con un backend en Python (FastAPI en puerto 8000), orquestación con n8n y análisis cualitativo con Google Gemini.

Tu objetivo principal prioritario en esta sesión es perfeccionar y consolidar el Motor Autónomo de Trading antes de iniciar la construcción de la interfaz gráfica (GUI).

Contexto Técnico del Backend:

1\. Tenemos un motor FastAPI con los siguientes endpoints disponibles:

   \- GET http\://localhost:8000/health

   \- POST http\://localhost:8000/scan-market

   \- POST http\://localhost:8000/validate-risk

   \- POST http\://localhost:8000/execute-bracket

2\. Reglas de Riesgo del Motor:

   \- Riesgo máximo por operación: 1% de la cuenta.

   \- Stop Loss: Entrada \- (1.5 \* ATR).

   \- Take Profit: Entrada \+ (2.0 \* Distancia al Stop Loss).  
Prioridades de Desarrollo Planificadas:  
FASE 1 (INMEDIATA / PRIORITARIA) \- Motor Autónomo y Validación con Alpaca Paper:  
1\. Bucle de Escaneo Continuo: Crear el servicio worker/scheduler con capacidad de arranque y parada manual (start/stop) y un intervalo de escaneo rápido (ej. 15 segundos o configurable) para detectar oportunidades en tiempo real.  
2\. Conexión y Pruebas en Vivo con Alpaca Paper Trading: Validar la comunicación completa con Alpaca Paper API (consulta de balance, estado de cuenta, y ejecución directa de órdenes bracket OCO) comprobando la solidez de las herramientas.  
FASE 2 (POSTERIOR) \- Interfaz Gráfica (GUI):  
Una vez que el motor autónomo esté completamente probado y validado con Alpaca Paper, proceder con la construcción del frontend visual (Next.js/React \+ Tailwind CSS \+ Lucide Icons \+ Charts) para supervisión, panel de aprobación Human-in-the-Loop y visualización de gráficos.  
Por favor, analiza la estructura del proyecto en el directorio actual y comienza implementando los componentes necesarios para la Fase 1 (bucle autónomo con control start/stop a 15s y validación con Alpaca Paper).

---

# **6\. Verificación de Funcionalidad e Integración**

Posterior a la generación de la GUI en Antigravity, la validación del sistema completo debe seguir la siguiente matriz de prueba:

| Prueba | Endpoint / Componente | Resultado Esperado |
| :---- | :---- | :---- |
| **Check de Infraestructura** | `GET /health` | Retorno `{"status": "ok"}` con respuesta de API. |
| **Validación de Métricas** | `POST /validate-risk` | Cálculo correcto de posiciones sin exceder el \$1%\$ de riesgo. |
| **Aprobación de la GUI** | Componente Aprobación \$\\rightarrow\$ `POST /execute-bracket` | Envío exitoso de la orden OCO a Alpaca y actualización del Dashboard. |
| **Resistencia a Fallos** | Manejo de Errores de API | Despliegue de alertas visuales en la GUI ante fallos de conexión con el bróker. |


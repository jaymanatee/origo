# EXPERIMENTS.md — registro de Origo

Lo que hemos ido haciendo, probando y cambiando, de lo más antiguo a lo más reciente. Cada
entrada dice qué se hizo, qué pasó y qué decidimos. Los hallazgos útiles para el informe están
marcados con **[informe]**.

---

## 2026-11-23 · Arranque del proyecto

**Repo nuevo `origo`**, separado de ARCA (DGM-Eduardov2): otra asignatura, otros entregables.
El código reutilizado de ARCA se **copia**, no se instala como dependencia, porque el RAG
busca el corpus y el índice en rutas relativas al paquete.

- `origo/engine/`: el motor y las herramientas de ARCA (`origin_engine.py`, `tools.py`,
  `registry.py`, `schemas.py`).
- `origo/rag/`: el RAG de ARCA.
- `origo/eval/`: generador de problemas y verificador.
- `origo/agents/`: lo nuevo.
- `data/eval/`: `test.jsonl` y `test_ood.jsonl`.

Los imports se reescribieron con `sed` (`rlm.origin_engine` → `origo.engine.origin_engine`,
etc.).

**Configuración** en `.env` (fuera del repo, `chmod 600`): `ORIGO_LLM_BASE_URL` y
`ORIGO_LLM_API_KEY`. En el repo solo va `.env.example`, sin la clave.

**Alcance:**
- **Dentro:** un producto por pregunta y varios acuerdos por producto; pregunta sola o con
  Excel; datos que faltan; acuerdos no cubiertos ("no cubierto"); consultas de conocimiento;
  precios en otra moneda; declaración de origen con confirmación humana.
- **Fuera:** varios productos, PDF y precio FOB.

## 2026-11-23 · El servicio central (Qwen3.8-27B-FP8 en vLLM)

**Qué probamos.** La API del servicio del DGX: vLLM 0.29, compatible con OpenAI, en
`http://0.0.0.0:8001/v1`, modelo `qwen3.8-27b-fp8`, contexto de 65.536 tokens.

**Qué pasó.**
- **Herramientas nativas:** devuelve `tool_calls` con nombre y argumentos separados. No hace
  falta analizar texto como en ARCA.
- **Razonamiento:** activado por defecto, en el campo `reasoning`. Se desactiva con
  `extra_body={"chat_template_kwargs": {"enable_thinking": False}}`.
- Con la pregunta "¿qué es el CTSH?":
  - razonando con `max_tokens=300`, gastó todo en pensar y no respondió;
  - sin razonar, contestó rápido y **mal**, con total seguridad;
  - razonando con margen, también mal: confundió CTSH con CTH.
- Con una herramienta descrita llamó bien a `get_origin_rule`, pero su razonamiento creía que
  **TCA era el T-MEC**.

**Decisión.** **[informe]** El modelo no sabe el dominio de memoria, razone o no. Por eso:
- todos los agentes reciben el mismo contexto del dominio (`agents/prompts.py`);
- los datos salen solo de herramientas y del RAG;
- el razonamiento se decide por agente, y es una ablación.

## 2026-11-23 · `llm.py` y `state.py`

- **`origo/llm.py`.** `chat()` es la única puerta al modelo: usa el cliente oficial de OpenAI,
  con el muestreo recomendado por Qwen para cada modo y `seed=0`. `METER` registra tokens y
  segundos por ejecución y por agente, para comparar arquitecturas por coste.
- **`origo/state.py`.** Modelos Pydantic de lo que produce cada agente (`Material`, `Plan`,
  `StatementRequest`, `Issue`) y `OrigoState`, el estado del grafo. `rules` y `evaluations`
  llevan el reductor `merge` para que las escrituras en paralelo por acuerdo se combinen en vez
  de pisarse.

---

## 2026-11-24 · `Plan` completado

**Problema.** El primer `Plan` exigía producto y al menos un acuerdo. No podía representar
una consulta de conocimiento sin producto, y un acuerdo no cubierto (Mercosur) daba error de
validación.

**Cambio.**
- `kind` (`evaluate` o `knowledge`) y `out_of_scope` (lista de texto libre);
- campos de producto opcionales;
- `gaps()`, que calcula **en código** lo que falta para evaluar (código SA, precio, acuerdo)
  sin depender de que el modelo lo anote en `missing`.

## 2026-11-24 · Salida estructurada: primeras pruebas

**Qué hicimos.** `chat(..., schema=Modelo)` pasa el esquema JSON del modelo Pydantic en
`response_format`, y vLLM restringe la generación para que el JSON lo cumpla. El resultado se
valida con `Modelo.model_validate_json`.

**Qué pasó, sin prompt de sistema:**
1. **Omitió campos opcionales** (`agreements`, `out_of_scope`): la gramática solo obliga a los
   requeridos, y Pydantic no marca como requerido un campo con valor por defecto.
   → `_all_required()` marca todos los campos como requeridos en el esquema enviado.
2. **Bucle**: `"UE-KR", "UE-JP"` repetidos hasta agotar 2.048 tokens. No sabía que Reino Unido
   es `TCA`, y la gramática no le dejaba escribir lo que quería.
   → `max_length=4` en las listas. **[informe]** La gramática de vLLM **sí aplica `maxItems`**:
   el bucle se cortó en 4.
   → `chat` lanza un error claro si `finish_reason == "length"`, en vez de un JSON cortado.
3. Con todos los campos obligatorios, **rellenó la declaración con datos inventados** (factura,
   REX), porque nada le decía que la dejara en `null`.

## 2026-11-24 · Orquestador

`agents/prompts.py` (contexto del dominio compartido) y `agents/orchestrator.py` (pregunta →
`Plan`, sin razonamiento).

**Resultado** con las tres preguntas de prueba (bombas a Reino Unido y Mercosur; validez de
una declaración con Japón; sillas a Corea y Canadá sin datos): `TCA` + `Mercosur` fuera de
alcance, `knowledge` con `UE-JP`, y `gaps()` detectando código y precio que faltan. Sin acuerdos
repetidos ni declaración inventada.

## 2026-11-24 · Helper de herramientas (`agents/tooling.py`)

`use_tools()` es el bucle común: llama al modelo, ejecuta sus `tool_calls`, devuelve los
mensajes `tool` con su `tool_call_id` y repite hasta que contesta sin llamar a nada (máximo
5 rondas).

**Decisiones:**
- **Cada agente recibe solo sus herramientas.**
- **Rechaza las que requieren confirmación.** `issue_origin_statement` la ejecuta el grafo,
  después de la confirmación humana.
- **Sin las protecciones de ARCA:** las comprobaciones van en el verificador.

**Prueba:** "regla de las bombas 8413.70 para Reino Unido" → una llamada a `get_origin_rule`
con `hs_code=841370` y `agreement=TCA`, y el CTSH bien explicado (viene de la herramienta).

## 2026-11-24 · País por material, no origen

**Problema.** `Material.origin` era `UE`, `SOCIO` o `TERCERO`, pero `SOCIO` depende del acuerdo:
una tela coreana es `SOCIO` para `UE-KR` y `TERCERO` para `TCA`. Con una sola lista de
materiales para varios acuerdos, cualquier valor era erróneo para alguno.

**Cambio.** El material guarda el **país** (`country`), y `origin_for(agreement)` calcula en
código el origen que espera `evaluate_origin` para cada acuerdo (`PARTNER` da el socio de cada
uno). Limitación documentada: solo acumulación bilateral.

## 2026-11-24 · Extractor y el esquema que el modelo no veía

`agents/extractor.py` funciona en dos fases:
1. Conversión de divisas con `get_exchange_rate`, solo si una regex detecta otra moneda.
2. Salida estructurada `Extraction`.

No se combinan herramientas y `response_format` en una misma llamada.

**Qué pasó, en orden:**
1. **`country` como texto libre:** escribió "Francia", "Corea"… y todo acabó `TERCERO`.
   → `country` pasa a `Literal` (`UE`, `GB`, `JP`, `KR`, `CA`, `OTRO`, `DESCONOCIDO`).
2. **Con el `Literal`:** todo `DESCONOCIDO`.
3. **Con un campo libre `country_name` delante:** seguía todo `DESCONOCIDO`.
4. **Causa:** **[informe]** **vLLM no enseña el esquema al modelo**, solo restringe la
   generación. Comprobado: 30 tokens de entrada con esquema y sin él. El modelo nunca había leído
   las `description`, así que no sabía qué significaba `UE`; la gramática le obligaba a elegir
   entre siete valores sin explicación.
   → `chat()` añade el esquema (con sus `description`) al final del mensaje de sistema, sin
   modificar la lista de mensajes del agente.
   → Con eso: `UE`, `KR` y `OTRO`, y el origen por acuerdo correcto
   (`UE-KR`: `UE, SOCIO, TERCERO`; `TCA`: `UE, TERCERO, TERCERO`).
5. **El extractor intentaba contestar la pregunta del usuario** ("¿son originarias?") en la
   fase de herramientas.
   → La pregunta va enmarcada ("tu tarea es solo la lista de materiales") y el texto final de
   la fase 1 no pasa al historial.
6. **`missing` con "valor en euros de los tornillos" (6 USD, ya convertidos):** venía del
   orquestador, no del extractor.
   → El orquestador no anota nada de los materiales ni de importes en otra moneda, salvo que
   no haya materiales ni fichero (entonces sí los pide, y es correcto).

**[informe] Tres niveles de control de la salida:**
- **`description`:** texto que el modelo puede ignorar; en vLLM ni siquiera lo ve si no se pone
  en el prompt.
- **Esquema o gramática:** tipos, `enum`, requeridos y `maxItems`, aplicados token a token.
- **Pydantic, después de generar:** `gt`, validadores.

La gramática garantiza la forma, no el contenido. Forzar una forma que el modelo no entiende
**empeora** el contenido: lo muestran el bucle de acuerdos y el `DESCONOCIDO` para Francia.

## 2026-11-24 · Revisión del repo y limpieza

- **`.gitignore`:** git no admite comentarios al final de una línea, así que
  `origo/rag/corpus/   # ...` no ignoraba nada y se subieron 30 ficheros del corpus.
  → Comentarios en su propia línea. Corpus sacado del repo con `git rm -r --cached` (cada uno
  lo copia de ARCA).
- **`StatementRequest`:** campos `str` obligatorios sin `None` posible, así que la gramática
  obligaba a inventar factura o REX si el usuario no los daba.
  → Todos `str | None`, con descripciones. Lo que falta va a `missing`; el REX solo si el envío
  supera 6.000 €.
- **Dependencia que faltaba:** `rank-bm25`, que usa el RAG.
- **Código muerto borrado:** `engine/parser.py` (leer `<tool_call>` del texto ya no hace falta
  con herramientas nativas) y los ejemplos de clase `get_weather` y `calculator`.
- `ruff`: imports ordenados y formato.
- Sin tocar por ahora: lo de entrenamiento RL en `eval/` (se revisa en la evaluación) y el RAG
  a medio copiar (se completa con la ruta de conocimiento).

## 2026-11-24 · Extractor con Excel

**Prueba.** Excel con columnas en español ("Componente", "País fabricación", "Partida SA",
"Coste"), un valor en GBP y una fila sin código SA.

**Problema.** `pd.read_excel` convirtió la columna de códigos a número: `8501.10` → `8501.1`.
El validador lo rechazó, como debía.

**Cambio.**
- `read_excel(dtype=str)`.
- `_hs6` completa `dddd.d` → `dddd.d0`, para celdas guardadas como número en el propio Excel,
  que pierden el cero al guardar. No hay ambigüedad: una subpartida siempre tiene dos dígitos.

**Resultado.** Columnas bien interpretadas; 60 GBP → 71,19 €; el cableado sin código fuera y en
`missing`; el motor británico `SOCIO` para `TCA` y `TERCERO` para `UE-JP`.

## 2026-11-24 · No todo nodo es un agente: reglas en código

**Decisión.** **[informe]** El LLM solo donde aporta criterio.
- `get_origin_rule(hs_code, agreement)` no tiene nada que decidir, así que el **nodo de
  reglas** (`agents/rules.py`) la llama en código, sin LLM.
- En ARCA la mayoría de fallos eran argumentos mal copiados por el modelo ("origen de un
  material", "valor de un material").

**Prueba.** Ventiladores 8414.51:
- TCA: CTSH o MAXNOM 50 %;
- Japón: CTH, MAXNOM 55 % o RVC 50 % FOB.

Un solo conjunto de reglas en cada acuerdo.

## 2026-11-24 · Agente de origen

`agents/origin.py`: **una llamada al LLM por ejecución**, solo para lo que necesita criterio:
- `operations`: fabricación o solo envasado, etiquetado, pintado, limpieza;
- `subdivision`: solo si una regla tiene varias variantes dentro de la partida.

La llamada a `evaluate_origin` la **monta el código** con el estado (materiales con
`origin_for`): el modelo nunca copia precios ni materiales.

**Resultado** con las sillas (9401.61; madera francesa, tela coreana, tornillos chinos):
- **Originarias** en `UE-KR` y `TCA` por **CTH**: todos los materiales cambian de partida.
- **MAXNOM no se cumple** (62,67 % no originario), pero basta con una alternativa.
- **La tela cambia según el acuerdo** en la traza: "sin declaración de proveedor" en Corea,
  "tercer país" en Reino Unido.
- **[informe]** **El motor exige declaración del proveedor:** un material UE o del socio sin
  ella cuenta como no originario (aviso `proveedor_sin_declaracion`). Con declaraciones, las
  sillas cumplirían también MAXNOM en Corea (solo los tornillos, 4,3 %): consejo práctico para
  el usuario.

## 2026-11-24 · Verificador

`agents/verifier.py`: **código, sin LLM**. Comprueba que lo que escribieron los agentes sale del
usuario o de una herramienta:
- código SA y precio del producto;
- datos de la declaración;
- código, valor y país de cada material;
- el material que es el propio producto (`bom_problems` de ARCA);
- los errores de las evaluaciones.

Cada problema es un `Issue` para el agente que debe rehacer su parte, con un mensaje que dice
qué hacer. No corrige nada. Los datos que faltan no son `Issue`s: se piden al usuario.

Para comprobar valores convertidos, el extractor guarda ahora `conversions` (los resultados de
`get_exchange_rate`) en el estado.

**Prueba.**
- Cadena de las sillas sin tocar: sin problemas.
- Con un valor y un código manipulados a mano: dos `Issue` del extractor, cada uno con su
  mensaje.

## 2026-11-24 · Llamadas repetidas

**Qué pasó.** El extractor pidió dos veces la misma conversión (6 USD, septiembre de 2026).
No cambia el resultado, pero es una llamada de más a la API del BCE.

**Cambio.** `use_tools` contesta una llamada idéntica con el resultado anterior, sin ejecutarla,
y solo devuelve las llamadas distintas (ya no hay conversiones duplicadas).

**[informe]** El 27B también repite alguna llamada, mucho menos que el 1,7B de ARCA. Posible
métrica de eficiencia en la evaluación: repeticiones por ejecución, junto a los tokens y los
segundos del `METER`.

---

## Pendiente

1. **Redactor:** estado → respuesta al usuario, solo con los resultados y sin cifras propias.
2. **Reintentos:** que cada agente lea sus `issues` cuando el verificador le devuelve el trabajo.
3. **Grafo de LangGraph:** aristas condicionales, límite de intentos, confirmación humana y
   emisión de la declaración.
4. **Ruta de conocimiento:** RAG con el filtro por acuerdo de ARCA.
5. **Evaluación y comparaciones:** un agente frente a multi-agente; con y sin verificador;
   27B frente al 1,7B de ARCA; razonamiento por agente.

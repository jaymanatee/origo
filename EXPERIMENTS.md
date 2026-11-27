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

## 2026-11-25 · Redactor

`agents/writer.py`: el último agente con LLM. El código reúne los **hechos** del estado
(`_facts`: veredictos, reglas aplicadas, materiales, avisos, lo que falta) y el modelo solo los
redacta en el idioma del usuario. Las cifras ya vienen calculadas; el modelo no suma ni
convierte nada.

**Primera versión, qué pasó.** No inventó ninguna cifra, pero:
- ligó la tolerancia general (10 %) a MAXNOM: la tolerancia solo se aplica a los criterios de
  cambio de clasificación y nunca amplía un umbral de valor;
- tradujo CTH como "cambio de tarifa arancelaria";
- añadió un consejo propio ("necesario para la trazabilidad").

**Causa.** **[informe]** Igual que con el esquema: recibía códigos sin explicar
(`tolerance_pct`, `CTH`, `proveedor_sin_declaracion`) y los rellenaba con lo que creía.

**Cambio.** Las explicaciones las pone el código, en la entrada; la salida no se toca:
- `GLOSSARY` con los criterios en el prompt;
- cada aviso del motor sustituido en los hechos por su explicación (`WARNINGS`, sacadas del
  código del motor);
- `tolerance_pct` renombrado a `general_tolerance_pct`;
- regla de "ningún consejo propio".

## 2026-11-25 · Comprobación de cifras del redactor

**Qué.** `unsupported_numbers`: toda cifra del informe tiene que estar en los hechos, en la
pregunta o en el glosario. Para no ser demasiado estricta:
- cada número se lee con punto y con coma decimal, y como código sin puntos
  (4407.92 = 440792);
- se aceptan redondeos a 0, 1 o 2 decimales;
- los enteros de 0 a 10 pasan siempre (numeración, "4 dígitos").

Si hay cifras sin respaldo, el redactor **reescribe una vez** con un mensaje que dice cuáles. Si
siguen, se entrega igualmente y quedan anotadas en `unsupported_numbers`. El texto nunca se
edita.

**Prueba.** El informe de las sillas: ninguna cifra sin respaldo y una sola llamada. Tres cifras
inventadas a mano (45,30 EUR, 8414.51, 33 %): detectadas las tres.

## 2026-11-25 · Grafo con LangGraph

`origo/graph.py`:
- **Rutas:** consulta de conocimiento o datos que faltan → redactor; sin materiales →
  redactor; el resto → extractor → reglas → origen → verificador.
- **Arista del verificador:** sin `issues` → redactor; con `issues` → el primer agente que
  debe rehacer, y los posteriores se rehacen solos por las aristas normales; sin intentos
  (`MAX_ATTEMPTS=2`) → redactor.
- **Reintentos:** cada agente con LLM añade a su prompt sus `issues` del intento anterior
  (`issues_note`).
- **`build(verify=False)`** quita el verificador, para la comparación.
- **`ask(pregunta)`** ejecuta una pregunta de principio a fin.

**Tests.** `tests/test_graph.py` comprueba el orden de los nodos con nodos falsos
(`monkeypatch`), sin modelo ni red.

**Primera ejecución completa** (sillas, Corea + Reino Unido + Mercosur): el mismo informe
correcto, sin `issues`, sin cifras sin respaldo. **[informe]** Coste por agente según el `METER`:

| Agente | Llamadas | Tokens de entrada | Segundos |
|---|---|---|---|
| Orquestador | 1 | 1.540 | 2,1 |
| Extractor | 4 | 4.704 (54 %) | 9,3 |
| Origen | 1 | 768 | 1,0 |
| Redactor | 1 | 1.676 | 10,4 |
| **Total** | **7** | **8.688** | **22,8** |

- **El agente de origen es el más barato**, porque solo recibe lo que necesita para decidir.
- **El redactor tarda por la salida**, no por la entrada.
- **El extractor gasta 4 llamadas en convertir 6 USD:** pide la conversión, la repite, escribe
  un texto que se descarta y hace la salida estructurada. Mejora posible: que el modelo solo lea
  importe, moneda y mes, y que convierta el código.

## 2026-11-25 · Confirmación humana y emisión de la declaración

`agents/statement.py`:
- **`statement_gaps`, en código**, decide si la declaración se puede ofrecer: el usuario la
  pidió, se sabe para qué acuerdo, están todos los datos, el REX está si el envío supera
  6.000 €, el producto es originario y no quedan `issues`.
- **`confirm`** para el grafo con `interrupt`: enseña los datos y espera un sí o un no.
- **`issue`** emite con `issue_origin_statement`, usando los datos del estado.

`ask` monta un checkpointer (`InMemorySaver`, con los modelos Pydantic del estado permitidos) y
un `thread_id`, y reanuda con `Command(resume=respuesta)`.

**Detalle.** Al reanudar, el nodo `confirm` se ejecuta otra vez desde el principio (se vio en la
prueba). Por eso, antes del `interrupt` solo lee, y la emisión va en un nodo aparte.

**[informe]** Lo que en ARCA no se pudo garantizar con el modelo (emitir solo si es originario,
el REX por encima de 6.000 €, nunca con datos inventados) aquí lo garantiza el código, y la
persona confirma lo que el código no puede comprobar.

**Prueba real.** Factura F-2026-118, 2.400 €, TCA: el grafo se paró, se contestó "s" y se
escribió `outputs/origin_statements/F-2026-118.txt` con el texto oficial. Con "n" no se emite
nada (`{'confirmed': False}`).

**Limitación.** Una declaración por pregunta (una factura es un envío a un destino). Si se
pregunta por varios acuerdos sin decir para cuál es, se pide en vez de elegir.

## 2026-11-25 · El redactor varía entre ejecuciones

**Qué pasó.** Con la misma pregunta:
- en una ejecución explicó bien el CTH (cambio de partida, 4 dígitos);
- en otra lo explicó como "cambio de subpartida, 6 dígitos", y llamó al TCA "Acuerdo UE-UE".

Ninguno es una cifra, así que la comprobación de números no los detecta.

**Causas.**
- **Temperatura 0,7**, la recomendada por Qwen sin razonamiento: demasiado alta para un
  redactor que debe ser fiel.
- **Siglas en inglés y redacción en español:** el modelo las traducía de memoria.
- **Explicaba por su cuenta** cómo se cumple un criterio.

**Cambio.**
- `chat(temperature=...)` y 0,2 para el redactor;
- glosario con los nombres en español de los criterios y los acuerdos;
- regla de no explicar más allá de la traza del motor.

**Resultado.** Dos ejecuciones casi iguales, con CTH como "cambio de partida" y el TCA con su
nombre.

**[informe]** La comprobación de cifras no basta: el redactor puede equivocarse en conceptos
sin inventar números, y el mismo sistema varía de una ejecución a otra. En la evaluación hay
que medir la fidelidad del informe y repetir cada pregunta varias veces.

## 2026-11-25 · Dos fuentes de "lo que falta" se contradecían

**Qué pasó.** Envío de 2.400 € a Reino Unido: el grafo ofreció la declaración sin REX
(correcto: por debajo de 6.000 € no hace falta), pero el informe decía que faltaba el REX. Hubo
tres intentos de arreglarlo:
1. Regla en el orquestador para que no anotara datos de la declaración en `missing`: lo seguía
   haciendo.
2. Quitar del redactor el ejemplo "(e.g. data of the statement)", que le invitaba a pedir datos
   de la declaración: el informe se contradecía dentro del mismo párrafo ("no se requiere REX…
   sin embargo, el sistema indica que falta").
3. **Que lo que falta lo calcule solo el código.**

**Cambio.** El redactor ya no recibe el `missing` del orquestador, que es texto libre. Lo que
falta sale de:
- `plan.gaps()` (código SA, precio, acuerdo);
- la lista de materiales vacía;
- los materiales que el extractor dejó fuera (`materials_missing`);
- `statement_gaps()` para la declaración.

**Resultado.** "La declaración de origen está lista. No falta ningún dato."

**[informe]** Cada vez que un dato tenía dos fuentes, una en código y otra en texto libre de un
LLM, acababan contradiciéndose, y los parches de prompt solo reducían el problema. La solución
fue dejar una sola fuente, en código.

## 2026-11-25 · Notebooks

`ipykernel` en el grupo `dev`, para probar cosas en notebooks con el entorno del repo.

## 2026-11-26 · Tres fallos de las comprobaciones en código

Encontrados leyendo el código, antes de medir. Los tres falseaban resultados sin dar error.

**1. Un precio en otra moneda no se evaluaba nunca.** El orquestador deja `exw_price_eur` en
`null` si el precio no está en euros, porque lo convierte el extractor. Pero `gaps()` lo daba
como dato que falta y el grafo iba del orquestador al redactor, que pedía al usuario un precio
que ya había dado. Además, si el modelo copiaba "150 USD" como 150 €, el verificador lo daba
por bueno: solo miraba que el número estuviera en el texto (en ARCA, ese error cambió un
veredicto en `cambio-01`).
→ `Plan.exw_price_currency` guarda la moneda. `gaps(converting=True)` no cuenta el precio como
que falta antes del extractor, y después del extractor `gaps()` sí: si no se pudo convertir, el
grafo va al redactor, que dice que no se pudo convertir en vez de pedir el precio.
→ El verificador ya no acepta como euros los importes escritos en otra moneda ("6 USD",
"USD 6", "$6", "60 libras"): un valor en euros sale del texto en euros o de `get_exchange_rate`.
Vale para el precio, los materiales y el valor del envío.

**2. Los países cortos daban un `Issue` falso.** La comprobación del país solo miraba palabras
de más de 3 letras, así que "UK", "USA" o "EE.UU." no se encontraban nunca: el extractor se
repetía para nada, el informe salía con un problema sin resolver y la declaración quedaba
bloqueada.
→ Las palabras cortas cuentan, pero como palabra entera ("usa" no vale dentro de "causa"), y
sin palabras vacías ("de", "del"…), para que "Estados Unidos de América" no pase por un "de".

**3. Los códigos de 8 y 10 dígitos sin separador** ("84145100", habitual en Excel) no se
reconocían, con el mismo efecto que el 2.
→ `_codes` reconoce los códigos NC y TARIC y se queda con las 6 primeras cifras. Sin espacio
antes de las cifras de más: con espacio, "8414.51 8501.10" se leía como un solo código y el
material desaparecía.

**Tests.** `tests/test_checks.py` (verificador y lo que falta, con estados fijos) y tres rutas
nuevas en `tests/test_graph.py`. Los nuevos fallan con el código anterior, salvo los que vigilan
que el arreglo no deje pasar nada de más (entre ellos, la pregunta de las sillas sin ningún
problema).

---

## 2026-11-26 · Banco de ARCA en Origo

`origo/eval/bench.py` pasa por Origo las 20 tareas del banco de la fase 4 de ARCA
(`data/bench/tasks.jsonl`, copiadas sin cambios) con las mismas comprobaciones automáticas
(`matches`, copiada). Así el agente único y el sistema multiagente se miden igual. Todavía no
se ha lanzado.

Origo es un grafo y no un agente con una lista de pasos, así que su respuesta se lee de otra
forma:

- **La respuesta comprobada es el informe más la declaración emitida.** El agente de ARCA
  escribía el nombre del fichero en su respuesta; Origo emite después del redactor, así que se
  añade la línea que ve el usuario con el fichero.
- **Las herramientas prohibidas se leen del estado:** `issue_origin_statement` se usó si hay
  declaración emitida; `evaluate_origin`, si algún acuerdo tiene veredicto.
- **Se quita el Markdown antes de comprobar:** "**no son** originarias" tiene que valer como
  "no son originarias".
- **La confirmación es automática**, porque las tareas que piden declaración dicen "si es
  originario, emítela". Cada ejecución tiene su carpeta de declaraciones vacía:
  `issue_origin_statement` no sobrescribe, y la segunda repetición encontraría la primera.

Cada fila se guarda al terminar su tarea, con la respuesta, el coste por agente (`ask()` acepta
ahora el nombre de la ejecución, para leer el `METER` aunque falle) y una traza del estado.
`--resume` continúa una ejecución cortada y `--rescore` vuelve a comprobar sin el modelo.

**Lo que se sabe antes de lanzarlo**, leyendo el código:
- `conocimiento-01` a `-03` necesitan el RAG, y `vies-01`, VIES (la herramienta está en
  `engine/tools.py`, pero ningún agente la usa).
- `faltan-datos-02` seguramente falle por dos motivos: el dong vietnamita no se reconoce como
  moneda extranjera (el extractor y el verificador solo conocen las del BCE), y el grafo evalúa
  con la lista de materiales incompleta, que es justo lo que la tarea prohíbe.

## 2026-11-27 · RAG, bloque 1: recuperación con filtro por acuerdo

La copia del RAG era anterior al filtro por acuerdo. Se ha puesto al día, sin tocar todavía el
grafo:

- `origo/rag/agreements.py` (el filtro) y `retriever.py` (los buscadores con `keep`);
- `origo/rag/serving.py`: la configuración elegida (chunking estructural, híbrido con λ = 0,3,
  pool de 20, filtro activado);
- `origo/rag/evaluate.py`, `evaluate_filter.py` y el conjunto dorado (`origo/rag/gold/`);
- los tests de filtro, recuperación y chunking.

El índice se guarda en `origo/rag/index/` (en el `.gitignore`), no en `rag/index`: esa ruta
relativa habría creado en Origo una carpeta fuera del `.gitignore`.

**pypdf fijado en 6.18.1.** Con la 7.1.1, una cita del conjunto dorado (q51, la guía de la AEAT
sobre el exportador autorizado) no aparecía en el texto extraído del PDF. Cambiar la cita habría
tapado el síntoma: si pypdf extrae distinto ese PDF, también puede trocear distinto los demás
sin que nada avise. Con la versión fijada, el corpus es el mismo con el que se eligió la
configuración.

**Resultado** (58 preguntas con respuesta en el corpus; 51 nombran un acuerdo):

| Buscador | Filtro | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---|---|---|---|---|
| BM25 | no | 0,371 | 0,491 | 0,560 | 0,681 | 0,472 |
| BM25 | sí | 0,414 | 0,534 | 0,621 | 0,690 | 0,522 |
| Denso | no | 0,483 | 0,741 | 0,879 | 0,914 | 0,659 |
| Denso | sí | 0,483 | 0,750 | 0,897 | 0,914 | 0,664 |
| Híbrido | no | 0,603 | 0,741 | 0,853 | 0,914 | 0,715 |
| Híbrido | sí | 0,603 | 0,819 | **0,905** | 0,914 | **0,729** |

- **[informe]** Sin filtro, el top 5 del híbrido trae 38 pasajes de otro acuerdo en total; con
  filtro, ninguno. Los artículos de los distintos protocolos se parecen mucho (todos hablan de
  conservar copias "durante X años"), así que sin filtro el modelo puede recibir el plazo de
  otro acuerdo. En q02 (conservación en el TCA), el pasaje bueno salía séptimo, por detrás de
  tres de otros acuerdos, y fuera de los 5 que recibe el modelo.
- El filtro mejora seis preguntas (q02, q03, q04, q05, q07 y q45) y no empeora ninguna.
- λ = 0,3 sigue siendo el mejor peso con el filtro y sin él.
- Control de la copia: BM25 da las mismas cifras que en ARCA; el denso cambia en el segundo
  decimal porque los embeddings se han recalculado.

Esto mide solo la búsqueda, sin modelo de lenguaje. La respuesta de Origo se medirá al meter el
agente de normativa en el grafo (bloque 2), con las tareas de conocimiento del banco.

## 2026-11-27 · RAG, bloque 2: agente de normativa en el grafo

El RAG entra en el grafo con un agente nuevo, `knowledge` (`origo/agents/knowledge.py`), y la
búsqueda que usa (`origo/rag/search.py`). Todavía no se ha medido con el modelo.

**El plan admite preguntas mixtas.** `Plan.knowledge_questions` lleva las preguntas sobre las
normas, cada una reescrita para entenderse sola. `kind` se queda: `evaluate` hay un producto
(con preguntas de normativa o sin ellas) y `knowledge` solo hay preguntas de normativa. Así una
pregunta como "¿son originarias las cocinas para el CETA y cuánto tiempo guardo los papeles?"
se evalúa y se responde, y el resto del código, que mira `kind`, no cambia.

**El filtro sale de los acuerdos del plan, no del texto.** En ARCA el filtro miraba qué acuerdo
nombraba la pregunta. Aquí la subpregunta ("¿cuánto tiempo guardo los papeles?") puede no
nombrarlo, y saldría el plazo de Corea o del Reino Unido. El orquestador ya sabe el acuerdo de
todo el mensaje: `search(pregunta, plan.agreements)` pasa los documentos de esos acuerdos y los
generales (guías de REX y de exportador autorizado, notas NC). Sin acuerdo, no se filtra. Si el
mensaje solo habla de acuerdos no cubiertos (Australia), no se busca nada: el corpus
contestaría con la norma de otro acuerdo.

**El agente.** Para cada subpregunta, el código busca los 5 mejores pasajes (lo medido con
R@5) y el modelo responde solo con ellos, con salida estructurada: si los pasajes la responden
(`found`), la respuesta y los números de los pasajes que cita. Si no la responden, no hay
respuesta: no contesta de memoria.

**El grafo.** El agente va después de la evaluación, y no antes ni en paralelo: el verificador
devuelve el trabajo a un agente y se rehacen los que van detrás, así que una cita mala solo
rehace el agente de normativa. Todas las preguntas pasan por el verificador (es código, no
cuesta nada), también las que no tienen nada que evaluar, y ya no hay salto directo al
redactor. Una pregunta solo de acuerdos no cubiertos (solo Mercosur) ya no pasa por el
extractor ni por el agente de origen: eran dos llamadas para nada.

**Verificador.** Comprueba que cada respuesta cita algún pasaje, que las citas existen y que
los pasajes son de los acuerdos del plan o de documentos generales. También que un plan
`knowledge` traiga sus preguntas: sin ellas el agente no se ejecuta y el redactor no tendría
nada que contar.

**Redactor.** Recibe cada respuesta con solo los pasajes que cita, como fuentes, y las nombra.
La comprobación de cifras cuenta esos pasajes como fuente.

**Tests.** `tests/test_knowledge.py` (filtro, agente, comprobaciones y hechos del redactor, con
búsqueda y modelo falsos) y las rutas nuevas en `tests/test_graph.py` (solo normativa, mixta,
mixta con datos que faltan, una cita mala rehace solo la normativa, sin verificador).

**Falta:** medirlo con las 6 tareas de `data/bench/tasks_knowledge.jsonl` y las 3 de
`conocimiento` del banco.

## 2026-11-27 · Primera medida de la normativa

**Resultado** (3 repeticiones; banco de `origo/eval/bench.py`):

| Tareas | Aciertos | Llamadas | Segundos por tarea |
|---|---|---|---|
| `kn-01` a `kn-06` (`tasks_knowledge.jsonl`) | **18 de 18** | 3,7 | 17 |
| `conocimiento-01` a `-03` (`tasks.jsonl`) | 6 de 9 | 4,6 | 27 |

- Las mixtas (`kn-05`, `kn-06`, `conocimiento-02` y `-03`: evaluar un producto y responder sobre
  las normas) aciertan siempre.
- **`conocimiento-01` falla las tres veces**, así que no es ruido. Pide el límite de los pequeños
  paquetes a Corea (1.000 USD, art. 21(3)(b)) **y ese límite en euros** con el tipo del BCE de
  octubre de 2026 (892,73 €). Ningún agente encadena una respuesta del corpus con un cálculo: el
  extractor solo convierte los importes del usuario, y el redactor no calcula.
- **[informe]** El sistema no se inventa la cifra: da el límite en USD con su fuente y dice que
  no puede dar la conversión. La comprobación de cifras del redactor habría rechazado un importe
  en euros que no estuviera en los hechos.

**Probado y quitado: convertir en el agente de normativa.** Se añadió un paso que convertía con
`get_exchange_rate` cuando la subpregunta pedía euros y la respuesta daba otra moneda. En las
tres repeticiones no se llegó a ejecutar, por dos motivos:
- el orquestador **separa la conversión en otra subpregunta** ("¿cuál es el equivalente en
  euros…?"), o la quita: la subpregunta del límite ya no pide euros, y la de la conversión se
  busca en el corpus, que no la responde (`not_found`);
- el modelo escribe el importe como "1000 US dollars", que no se reconocía como moneda.

Hacerlo funcionar pedía cambiar también el prompt del orquestador y el reconocimiento de
monedas, para una sola tarea del banco. Se deja como **límite conocido**: Origo no encadena una
respuesta de normativa con un cálculo.

## Pendiente

1. **Evaluación y comparaciones:** un agente frente a multi-agente; con y sin verificador;
   27B frente al 1,7B de ARCA; razonamiento por agente; fidelidad y variación del redactor
   (varias ejecuciones por pregunta). El script del banco está hecho; falta lanzarlo y el
   baseline de ARCA con el 27B.
2. **Ruta de conocimiento:** medida (18 de 18 y 6 de 9). Límite conocido: no encadena una
   respuesta del corpus con una conversión (`conocimiento-01`).
3. **Monedas fuera del BCE y lista de materiales incompleta** (`faltan-datos-02`), y decidir si
   VIES entra en el alcance.
4. **Extractor:** que el modelo solo lea importe, moneda y mes, y que convierta el código
   (de 4 llamadas a 1), medido con el `METER`.
5. **Para pulir:**
   - el aviso `proveedor_sin_declaracion` debería decir qué materiales lo provocan;
   - el hueco vacío "(Exporter Reference No )" en las declaraciones sin REX;
   - varias declaraciones en una pregunta.
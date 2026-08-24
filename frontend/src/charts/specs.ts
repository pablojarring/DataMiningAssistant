/**
 * Especificaciones de Vega-Lite para el dashboard de perfilado.
 *
 * Están todas acá y no dentro de los componentes por una razón concreta: cada
 * una es una función pura de `perfil -> spec`, así que se puede leer, comparar
 * y ajustar la gramática de los gráficos sin abrirse paso entre JSX. Es también
 * la ventaja de Vega-Lite sobre una librería de componentes: el gráfico es un
 * dato, no un árbol de elementos.
 *
 * Los datos llegan ya agregados desde el backend (bins, cuartiles, top-K), así
 * que ninguna spec calcula nada sobre filas crudas — el navegador nunca ve el
 * dataset completo.
 */

import type { TopLevelSpec } from "vega-lite";

import type { ColumnProfile, Correlations } from "../api";

const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
const ACCENT = "#3b6ea5";

/** Config común: tipografía del sitio y menos tinta de la que Vega pone por defecto. */
const CONFIG = {
  font: FONT,
  axis: { labelColor: "#57606a", titleColor: "#57606a", grid: false, domainColor: "#d8d8d4" },
  view: { stroke: null },
  legend: { labelColor: "#57606a", titleColor: "#57606a" },
} as const;

const SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json";

/**
 * Ajuste solo horizontal, para los graficos cuya altura crece con la cantidad
 * de barras (`height: { step }`).
 *
 * Con `width: "container"`, Vega intenta ajustar los dos ejes y despues avisa
 * por consola que descarta el vertical porque la altura es discreta. El aviso
 * es correcto pero inutil, y una consola llena de avisos que hay que ignorar es
 * una consola en la que ya no se ve el error que si importa.
 */
const FIT_X = { type: "fit-x", contains: "padding" } as const;

export function nullsSpec(columns: ColumnProfile[]): TopLevelSpec {
  return {
    $schema: SCHEMA,
    config: CONFIG,
    autosize: FIT_X,
    // Solo las columnas con algún nulo: una barra de 0% por cada columna sana
    // llena el gráfico de ruido y esconde justo lo que se viene a mirar.
    data: {
      values: columns
        .filter((column) => column.null_count > 0)
        .map((column) => ({
          columna: column.name,
          porcentaje: column.null_fraction * 100,
          nulos: column.null_count,
        })),
    },
    width: "container",
    height: { step: 20 },
    mark: { type: "bar", color: "#d97757", cornerRadiusEnd: 2 },
    encoding: {
      y: { field: "columna", type: "nominal", sort: "-x", title: null },
      x: {
        field: "porcentaje",
        type: "quantitative",
        title: "% de filas sin dato",
        // Escala fija de 0 a 100: con escala automática, una columna con 0,3%
        // de nulos dibuja una barra que ocupa todo el ancho y parece alarmante.
        scale: { domain: [0, 100] },
      },
      tooltip: [
        { field: "columna", type: "nominal", title: "Columna" },
        { field: "nulos", type: "quantitative", title: "Filas sin dato" },
        { field: "porcentaje", type: "quantitative", format: ".2f", title: "%" },
      ],
    },
  };
}

export function correlationSpec(correlations: Correlations): TopLevelSpec {
  const cells = correlations.columns.flatMap((rowName, i) =>
    correlations.columns.map((columnName, j) => ({
      fila: rowName,
      columna: columnName,
      valor: correlations.matrix[i][j],
    })),
  );

  return {
    $schema: SCHEMA,
    config: CONFIG,
    data: { values: cells },
    width: "container",
    encoding: {
      x: { field: "columna", type: "nominal", title: null, sort: correlations.columns },
      y: { field: "fila", type: "nominal", title: null, sort: correlations.columns },
    },
    layer: [
      {
        mark: { type: "rect" },
        encoding: {
          color: {
            field: "valor",
            type: "quantitative",
            // Escala divergente centrada en 0 y fijada en [-1, 1]: con una
            // escala automática, un tablero donde todo ronda 0,1 se pintaría con
            // los mismos colores intensos que uno con correlaciones de 0,95.
            scale: { scheme: "blueorange", domain: [-1, 1], reverse: true },
            legend: { title: "r de Pearson" },
          },
          tooltip: [
            { field: "fila", type: "nominal", title: "Fila" },
            { field: "columna", type: "nominal", title: "Columna" },
            { field: "valor", type: "quantitative", format: ".3f", title: "r" },
          ],
        },
      },
      {
        mark: { type: "text", fontSize: 10 },
        encoding: {
          text: { field: "valor", type: "quantitative", format: ".2f" },
          color: {
            // Texto oscuro sobre celdas pálidas y claro sobre las saturadas: sin
            // esto, los valores cerca de ±1 quedan ilegibles sobre su propio color.
            condition: { test: "abs(datum.valor) > 0.6", value: "#ffffff" },
            value: "#3d3d3a",
          },
        },
      },
    ],
  };
}

export function histogramSpec(column: ColumnProfile): TopLevelSpec {
  return {
    $schema: SCHEMA,
    config: CONFIG,
    data: {
      values: (column.histogram ?? []).map((bin) => ({
        inicio: bin.bin_start,
        fin: bin.bin_end,
        conteo: bin.count,
      })),
    },
    width: "container",
    height: 110,
    mark: { type: "bar", color: ACCENT },
    encoding: {
      // `binned: true` le dice a Vega-Lite que los bins ya vienen calculados y
      // que use `inicio`/`fin` como bordes. Sin esto intentaría re-binear los
      // bordes como si fueran valores sueltos.
      x: { field: "inicio", type: "quantitative", bin: { binned: true }, title: null },
      x2: { field: "fin" },
      y: { field: "conteo", type: "quantitative", title: "filas" },
      tooltip: [
        { field: "inicio", type: "quantitative", format: ".4~s", title: "Desde" },
        { field: "fin", type: "quantitative", format: ".4~s", title: "Hasta" },
        { field: "conteo", type: "quantitative", title: "Filas" },
      ],
    },
  };
}

/**
 * Boxplot armado a mano desde estadísticas ya calculadas.
 *
 * El `mark: "boxplot"` de Vega-Lite calcula los cuartiles él mismo y por lo
 * tanto necesita las filas crudas — que es justamente lo que no le mandamos al
 * navegador. Con min/p25/p50/p75/max ya calculados por DuckDB, el mismo dibujo
 * sale de tres capas: bigote, caja y mediana.
 */
export function boxplotSpec(column: ColumnProfile): TopLevelSpec {
  const row = {
    min: column.min as number,
    p25: column.p25 as number,
    p50: column.p50 as number,
    p75: column.p75 as number,
    max: column.max as number,
  };

  return {
    $schema: SCHEMA,
    config: CONFIG,
    data: { values: [row] },
    width: "container",
    height: 44,
    encoding: {
      x: {
        field: "min",
        type: "quantitative",
        title: null,
        // El dominio no arranca en cero: un boxplot muestra dispersión, y forzar
        // el cero aplasta la caja contra el borde cuando los valores son altos.
        scale: { zero: false },
      },
    },
    layer: [
      { mark: { type: "rule", color: "#8a8a85" }, encoding: { x2: { field: "max" } } },
      {
        mark: { type: "bar", height: 16, color: ACCENT, opacity: 0.85 },
        encoding: { x: { field: "p25", type: "quantitative" }, x2: { field: "p75" } },
      },
      {
        mark: { type: "tick", thickness: 2, size: 22, color: "#ffffff" },
        encoding: {
          x: { field: "p50", type: "quantitative" },
          tooltip: [
            { field: "min", type: "quantitative", format: ".4~s", title: "Mínimo" },
            { field: "p25", type: "quantitative", format: ".4~s", title: "Q1" },
            { field: "p50", type: "quantitative", format: ".4~s", title: "Mediana" },
            { field: "p75", type: "quantitative", format: ".4~s", title: "Q3" },
            { field: "max", type: "quantitative", format: ".4~s", title: "Máximo" },
          ],
        },
      },
    ],
  };
}

export function topValuesSpec(column: ColumnProfile): TopLevelSpec {
  return {
    $schema: SCHEMA,
    config: CONFIG,
    autosize: FIT_X,
    data: {
      values: (column.top_values ?? []).map((entry) => ({
        valor: String(entry.value),
        conteo: entry.count,
      })),
    },
    width: "container",
    height: { step: 18 },
    mark: { type: "bar", color: ACCENT, cornerRadiusEnd: 2 },
    encoding: {
      y: { field: "valor", type: "nominal", sort: "-x", title: null },
      x: { field: "conteo", type: "quantitative", title: "filas" },
      tooltip: [
        { field: "valor", type: "nominal", title: "Valor" },
        { field: "conteo", type: "quantitative", title: "Filas" },
      ],
    },
  };
}

export function booleanSpec(column: ColumnProfile): TopLevelSpec {
  const values = [
    { valor: "true", conteo: column.true_count ?? 0 },
    { valor: "false", conteo: column.false_count ?? 0 },
  ];
  if (column.null_count > 0) {
    values.push({ valor: "sin dato", conteo: column.null_count });
  }

  return {
    $schema: SCHEMA,
    config: CONFIG,
    autosize: FIT_X,
    data: { values },
    width: "container",
    height: { step: 22 },
    mark: { type: "bar", cornerRadiusEnd: 2 },
    encoding: {
      y: { field: "valor", type: "nominal", title: null, sort: ["true", "false", "sin dato"] },
      x: { field: "conteo", type: "quantitative", title: "filas" },
      color: {
        field: "valor",
        type: "nominal",
        scale: { domain: ["true", "false", "sin dato"], range: [ACCENT, "#8fb0d0", "#d97757"] },
        legend: null,
      },
      tooltip: [
        { field: "valor", type: "nominal", title: "Valor" },
        { field: "conteo", type: "quantitative", title: "Filas" },
      ],
    },
  };
}

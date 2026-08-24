import { useEffect, useRef } from "react";
import type { TopLevelSpec } from "vega-lite";

/**
 * Dibuja una especificación de Vega-Lite dentro de un div.
 *
 * El import de `vega-embed` es dinámico a propósito: Vega + Vega-Lite pesan más
 * que todo el resto de la aplicación junta. Cargándolos solo cuando hay algo que
 * graficar, Vite los emite en un chunk aparte y la pantalla de subida —que no
 * tiene ningún gráfico— sigue arrancando liviana.
 *
 * La vista de Vega se destruye en la limpieza del efecto. Sin ese `finalize`,
 * cada re-render dejaría atrás una vista viva con sus listeners y su animación:
 * la fuga de memoria clásica al envolver una librería imperativa en React.
 */
export function VegaChart({ spec, className }: { spec: TopLevelSpec; className?: string }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    let finalize: (() => void) | null = null;

    void import("vega-embed").then(async ({ default: embed }) => {
      if (cancelled || !container.current) return;
      const result = await embed(container.current, spec, {
        actions: false,
        // SVG y no canvas: los gráficos quedan nítidos en pantallas HiDPI y el
        // texto es seleccionable. A esta escala (decenas de marcas por gráfico)
        // el costo de rendimiento no se nota.
        renderer: "svg",
      });
      if (cancelled) {
        result.finalize();
        return;
      }
      finalize = () => result.finalize();
    });

    return () => {
      cancelled = true;
      finalize?.();
    };
  }, [spec]);

  // `w-full` no es decorativo: las specs usan `width: "container"`, y Vega mide
  // este div para decidir el ancho del grafico. Si no tuviera un ancho resuelto,
  // dibujaria un SVG de cero pixeles.
  return <div className={className ?? "mt-3 w-full"} ref={container} />;
}

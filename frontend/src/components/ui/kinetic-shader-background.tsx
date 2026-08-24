"use client";

// KineticShaderBackground — la mezcla de los dos componentes de 21st.dev.
//
// El plasma de WebGL da el color y el movimiento lento de fondo; la grilla
// cinética, dibujada encima con fondo transparente, da la reacción al cursor y
// las ondas al hacer clic. Ninguno de los dos se ve como se veía solo: el
// plasma deja de ser un degradado plano porque tiene una malla que responde, y
// la grilla deja de flotar sobre un gris porque ahora late.
//
// Orden de capas:
//   0. shader   — canvas de WebGL, fijo al viewport
//   1. grilla   — canvas 2D transparente, fijo al viewport
//   2. velo     — degradado oscuro que mantiene legible el contenido
//   3. children — la app
//
// El velo no es decorativo. Un plasma a plena intensidad detrás de tablas de
// números y ejes de gráficos es ilegible: el contraste del texto cambia según
// dónde caiga la onda en ese momento, y un dato que se lee peor cuando el fondo
// pasa por su color claro es un dato que la interfaz está escondiendo.

import { useEffect, useState, type ReactNode } from "react";

import { KineticGrid } from "@/components/ui/kinetic-grid";
import { ShaderBackground } from "@/components/ui/shader-background";
import { cn } from "@/lib/utils";

/** `true` si el sistema pide reducir el movimiento; reactivo al cambio. */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() =>
    typeof window === "undefined"
      ? false
      : window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

export function KineticShaderBackground({
  children,
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  const reducedMotion = usePrefersReducedMotion();
  // Dos canvas a pantalla completa animándose son exactamente el tipo de efecto
  // que la preferencia del sistema pide apagar. Se sigue dibujando el fondo —
  // quitarlo cambiaría el diseño— pero congelado.
  const animated = !reducedMotion;

  return (
    <div className={cn("relative min-h-screen w-full", className)}>
      {/* `fixed` y no `absolute`: el fondo tiene que quedarse quieto mientras
          el dashboard hace scroll, no estirarse a lo largo de toda la página. */}
      <div className="fixed inset-0 z-0 bg-[#05070d]">
        <ShaderBackground className="h-full w-full" animated={animated} />
      </div>

      <KineticGrid className="fixed inset-0 z-0" animated={animated} />

      <div
        aria-hidden="true"
        className="fixed inset-0 z-0 bg-gradient-to-b from-[#05070d]/70 via-[#05070d]/55 to-[#05070d]/80"
      />

      <div className="relative z-10">{children}</div>
    </div>
  );
}

export default KineticShaderBackground;

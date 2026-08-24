"use client";

// KineticGrid — grilla que se deforma hacia el cursor y ondula con cada clic.
//
// Adaptado del componente de 21st.dev. Tres diferencias con el original, todas
// porque acá va **encima** del shader y no solo:
//
//  1. No pinta fondo. El original arranca cada fotograma con un `fillRect`
//     opaco; ese relleno es justamente lo que taparía el plasma. Sin él, la
//     grilla flota sobre el shader y los dos efectos se ven a la vez — que es
//     todo el punto de la mezcla.
//  2. Escala por `devicePixelRatio`. El original dimensiona el canvas en
//     píxeles CSS, así que en una pantalla HiDPI cada línea de 0,8 px se dibuja
//     con la mitad de resolución de la que tiene la pantalla y se ve borrosa
//     al lado del texto nítido de al lado.
//  3. Se saltea el fotograma cuando no hay nada que cambiar. El original
//     redibuja ~500 nodos y ~1000 segmentos 60 veces por segundo aunque el
//     mouse esté quieto. Detrás de un dashboard que ya tiene un shader y varios
//     gráficos corriendo, eso es calentar la máquina para dibujar lo mismo.

import { useCallback, useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

// ─── Tipos ────────────────────────────────────────────────────────────────────

interface Point {
  x: number;
  y: number;
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  opacity: number;
  born: number;
}

// ─── Constantes ───────────────────────────────────────────────────────────────

const CELL_SIZE = 55;
const INFLUENCE_RADIUS = 260;
const MAX_WARP = 24;
const DOT_SPACING = 28;
const LERP_SPEED = 0.08;
/** Debajo de esto el cursor se considera quieto y se deja de redibujar. */
const SETTLE_EPSILON = 0.5;

const LINE_BASE = { r: 255, g: 255, b: 255, a: 0.1 };
const NODE_BASE_RADIUS = 1.8;
const NODE_ACTIVE_RADIUS = 3.2;

// El cian del shader, para que la grilla no parezca pegada encima sino parte de
// la misma escena. Es el mismo tono que la paleta del plasma (0, 0.898, 1).
const LINE_ACTIVE = { r: 94, g: 214, b: 255, a: 0.9 };
const NODE_ACTIVE = { r: 160, g: 236, b: 255, a: 1.0 };
const GLOW_RGB = "94,214,255";
const RIPPLE_RGB = "120,222,255";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function lerpN(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

function lerpColor(
  base: { r: number; g: number; b: number; a: number },
  active: { r: number; g: number; b: number; a: number },
  t: number,
): string {
  const r = Math.round(lerpN(base.r, active.r, t));
  const g = Math.round(lerpN(base.g, active.g, t));
  const b = Math.round(lerpN(base.b, active.b, t));
  const a = lerpN(base.a, active.a, t);
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

// ─── Componente ───────────────────────────────────────────────────────────────

export function KineticGrid({
  className,
  animated = true,
}: {
  className?: string;
  /** En false dibuja una grilla en reposo y no escucha al cursor. */
  animated?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const mouseRef = useRef<Point>({ x: -9999, y: -9999 });
  const targetMouseRef = useRef<Point>({ x: -9999, y: -9999 });
  const ripplesRef = useRef<Ripple[]>([]);
  const rafRef = useRef<number>(0);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  const dirtyRef = useRef(true);

  // ── Deformación ─────────────────────────────────────────────────────────────

  const getWarpedPoint = useCallback(
    (
      gx: number,
      gy: number,
      col: number,
      row: number,
      mouse: Point,
      ripples: Ripple[],
      cols: number,
      rows: number,
    ): { pt: Point; proximity: number } => {
      // Anclaje del borde: fija progresivamente las filas y columnas del
      // perímetro, para que la grilla no se despegue de los bordes del canvas.
      const edgeMargin = 1.5;
      const colPin = Math.min(col / edgeMargin, (cols - 1 - col) / edgeMargin, 1);
      const rowPin = Math.min(row / edgeMargin, (rows - 1 - row) / edgeMargin, 1);
      const pinFactor = colPin * colPin * rowPin * rowPin;

      const dx = gx - mouse.x;
      const dy = gy - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);

      const proximity = Math.max(0, 1 - dist / INFLUENCE_RADIUS) * pinFactor;

      let rx = 0;
      let ry = 0;
      for (const r of ripples) {
        const rdx = gx - r.x;
        const rdy = gy - r.y;
        const rdist = Math.sqrt(rdx * rdx + rdy * rdy);
        const waveWidth = 55;
        const diff = rdist - r.radius;
        if (Math.abs(diff) < waveWidth) {
          const strength =
            (1 - Math.abs(diff) / waveWidth) * r.opacity * 18 * pinFactor;
          const angle = Math.atan2(rdy, rdx);
          const sign = diff < 0 ? -1 : 1;
          rx += Math.cos(angle) * strength * sign * -1;
          ry += Math.sin(angle) * strength * sign * -1;
        }
      }

      if (dist < INFLUENCE_RADIUS && dist > 0 && pinFactor > 0) {
        const t = dist / INFLUENCE_RADIUS;
        const eased = t < 0.01 ? 0 : (1 - t) * (1 - t) * Math.min(1, dist / 60);
        const warpAmt = eased * MAX_WARP * pinFactor;
        const angle = Math.atan2(dy, dx);
        return {
          pt: {
            x: gx - Math.cos(angle) * warpAmt + rx,
            y: gy - Math.sin(angle) * warpAmt + ry,
          },
          proximity,
        };
      }

      return { pt: { x: gx + rx, y: gy + ry }, proximity };
    },
    [],
  );

  // ── Dibujo ──────────────────────────────────────────────────────────────────

  const draw = useCallback(
    (now: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const { w: W, h: H } = sizeRef.current;
      const mouse = mouseRef.current;
      const ripples = ripplesRef.current;

      // `clearRect` y NO un `fillRect` de color: deja el canvas transparente y
      // el shader de abajo se ve a través. Es la línea que hace la mezcla.
      ctx.clearRect(0, 0, W, H);

      // Textura de puntos fija, muy tenue: le da grano a las zonas planas del
      // plasma sin competir con la grilla.
      ctx.fillStyle = "rgba(255,255,255,0.05)";
      for (let x = DOT_SPACING / 2; x < W; x += DOT_SPACING) {
        for (let y = DOT_SPACING / 2; y < H; y += DOT_SPACING) {
          ctx.beginPath();
          ctx.arc(x, y, 0.7, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      for (let i = ripples.length - 1; i >= 0; i--) {
        const r = ripples[i];
        const age = (now - r.born) / 1000;
        r.radius = Math.max(0, age * 400);
        r.opacity = Math.max(0, 1 - age * 1.2);
        if (r.opacity <= 0) ripples.splice(i, 1);
      }

      const cols = Math.max(2, Math.ceil(W / CELL_SIZE)) + 1;
      const rows = Math.max(2, Math.ceil(H / CELL_SIZE)) + 1;
      const cellW = W / (cols - 1);
      const cellH = H / (rows - 1);

      const pts: Point[][] = [];
      const prox: number[][] = [];

      for (let row = 0; row < rows; row++) {
        pts[row] = [];
        prox[row] = [];
        for (let col = 0; col < cols; col++) {
          const { pt, proximity } = getWarpedPoint(
            col * cellW,
            row * cellH,
            col,
            row,
            mouse,
            ripples,
            cols,
            rows,
          );
          pts[row][col] = pt;
          prox[row][col] = proximity;
        }
      }

      const drawSeg = (p1: Point, p2: Point, pr1: number, pr2: number) => {
        const avg = (pr1 + pr2) / 2;
        const t = avg * avg * (3 - 2 * avg); // smoothstep
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.strokeStyle = lerpColor(LINE_BASE, LINE_ACTIVE, t);
        ctx.lineWidth = lerpN(0.8, 1.5, t);
        ctx.stroke();
      };

      ctx.lineCap = "butt";

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols - 1; col++) {
          drawSeg(
            pts[row][col],
            pts[row][col + 1],
            prox[row][col],
            prox[row][col + 1],
          );
        }
      }

      for (let col = 0; col < cols; col++) {
        for (let row = 0; row < rows - 1; row++) {
          drawSeg(
            pts[row][col],
            pts[row + 1][col],
            prox[row][col],
            prox[row + 1][col],
          );
        }
      }

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const p = pts[row][col];
          const pr = prox[row][col];
          const t = pr * pr * (3 - 2 * pr); // smoothstep
          const r = lerpN(NODE_BASE_RADIUS, NODE_ACTIVE_RADIUS, t);

          if (t > 0.3) {
            const glowR = r + lerpN(0, 6, (t - 0.3) / 0.7);
            const grd = ctx.createRadialGradient(
              p.x,
              p.y,
              r * 0.5,
              p.x,
              p.y,
              glowR,
            );
            grd.addColorStop(0, `rgba(${GLOW_RGB},${(t * 0.3).toFixed(3)})`);
            grd.addColorStop(1, `rgba(${GLOW_RGB},0)`);
            ctx.beginPath();
            ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
            ctx.fillStyle = grd;
            ctx.fill();
          }

          ctx.beginPath();
          ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
          ctx.fillStyle = lerpColor(
            { r: 255, g: 255, b: 255, a: 0.18 },
            NODE_ACTIVE,
            t,
          );
          ctx.fill();
        }
      }

      for (const r of ripples) {
        ctx.beginPath();
        ctx.arc(r.x, r.y, Math.max(0, r.radius), 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(${RIPPLE_RGB},${(r.opacity * 0.28).toFixed(3)})`;
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    },
    [getWarpedPoint],
  );

  // ── Bucle de animación ──────────────────────────────────────────────────────

  const animate = useCallback(
    (now: number) => {
      const m = mouseRef.current;
      const t = targetMouseRef.current;
      const moving = Math.hypot(t.x - m.x, t.y - m.y) > SETTLE_EPSILON;

      if (moving) {
        m.x = lerpN(m.x, t.x, LERP_SPEED);
        m.y = lerpN(m.y, t.y, LERP_SPEED);
      }

      // El fotograma se dibuja solo si algo cambió. Con el cursor quieto y sin
      // ondas, el canvas ya muestra el estado correcto: repintarlo daría el
      // mismo resultado gastando el mismo trabajo.
      if (moving || ripplesRef.current.length > 0 || dirtyRef.current) {
        draw(now);
        dirtyRef.current = false;
      }

      rafRef.current = requestAnimationFrame(animate);
    },
    [draw],
  );

  // ── Montaje ─────────────────────────────────────────────────────────────────

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const setSize = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      // El canvas se dimensiona en píxeles del dispositivo y el contexto se
      // escala, así que el resto del dibujo sigue razonando en píxeles CSS.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
      const ctx = canvas.getContext("2d");
      // `setTransform` y no `scale`: `scale` se acumula sobre la transformación
      // anterior, así que tras dos resizes el factor sería dpr².
      ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
      sizeRef.current = { w, h };
      dirtyRef.current = true;
    };

    setSize();
    window.addEventListener("resize", setSize);

    if (!animated) {
      // Sin movimiento: una grilla en reposo, dibujada una sola vez.
      draw(performance.now());
      return () => window.removeEventListener("resize", setSize);
    }

    const onMouseMove = (e: MouseEvent) => {
      targetMouseRef.current = { x: e.clientX, y: e.clientY };
    };

    const onClick = (e: MouseEvent) => {
      ripplesRef.current.push({
        x: e.clientX,
        y: e.clientY,
        radius: 0,
        opacity: 1,
        born: performance.now(),
      });
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        dirtyRef.current = true;
        if (rafRef.current === 0) rafRef.current = requestAnimationFrame(animate);
      } else if (rafRef.current !== 0) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
        // Las ondas en vuelo se descartan: al volver a la pestaña, sus tiempos
        // de nacimiento serían de hace minutos y aparecerían ya expiradas.
        ripplesRef.current = [];
      }
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("click", onClick);
    document.addEventListener("visibilitychange", onVisibility);
    rafRef.current = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener("resize", setSize);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("click", onClick);
      document.removeEventListener("visibilitychange", onVisibility);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
  }, [animate, animated, draw]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={cn("pointer-events-none", className)}
    />
  );
}

export default KineticGrid;

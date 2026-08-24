import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // Vega + Vega-Lite pesan ~850 kB minificados y no hay forma de adelgazarlos:
    // son un motor de visualizacion completo. Ya estan aislados en su propio
    // chunk (`import("vega-embed")` dinamico en VegaChart.tsx), asi que solo se
    // descargan al abrir un dashboard y no penalizan la pantalla de subida.
    // Subimos el umbral del aviso para que siga sirviendo de alarma si algun
    // dia crece el bundle *principal*, que es el que si importa.
    chunkSizeWarningLimit: 900,
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    watch: {
      // Sondeo en vez de eventos del sistema de archivos. Corriendo en Docker
      // sobre Windows, el bind mount no propaga los eventos de inotify al
      // contenedor: Vite nunca se entera de que un archivo cambio y sigue
      // sirviendo la version vieja desde su cache, ni siquiera con un recarga
      // forzada del navegador. El sondeo cuesta algo de CPU y es la unica forma
      // de que el HMR funcione en ese entorno.
      usePolling: true,
      interval: 300,
    },
  },
});

"use client";

// FileDropzone — zona de arrastrar y soltar con validación de tipo y tamaño.
//
// Adaptado del componente de 21st.dev. Tres diferencias con el original:
//
//  1. Es *controlado*. El original guarda el archivo en su propio estado; acá
//     el formulario de subida ya es dueño de ese estado, y dos fuentes de
//     verdad para el mismo archivo terminan desincronizadas en cuanto una de
//     las dos se resetea.
//  2. Sin vista previa de imagen. Un CSV o un Parquet no tienen miniatura, así
//     que esa mitad del componente era peso muerto — y con ella se fue la
//     necesidad de manejar object URLs y revocarlos.
//  3. El hook `use-file-upload` que importaba no venía incluido en el paquete
//     del catálogo. Está reimplementado abajo, con el contrato mínimo que esta
//     app necesita.

import { AlertCircle, FileSpreadsheet, Upload, X } from "lucide-react";
import { useRef, useState, type DragEvent } from "react";

import { cn } from "@/lib/utils";

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * Valida un archivo contra las extensiones aceptadas y el tamaño máximo.
 *
 * Se valida por extensión y no por `file.type`: los navegadores reportan el
 * MIME de un `.parquet` como cadena vacía, y el de un `.csv` va cambiando entre
 * `text/csv`, `application/vnd.ms-excel` y vacío según el sistema operativo y
 * qué programa lo tenga asociado. La extensión es lo único estable, y además es
 * exactamente lo que el backend usa para decidir cómo leerlo.
 */
function validate(file: File, accept: string, maxBytes: number): string | null {
  const extensions = accept
    .split(",")
    .map((value) => value.trim().toLowerCase())
    .filter(Boolean);
  const name = file.name.toLowerCase();

  if (extensions.length > 0 && !extensions.some((ext) => name.endsWith(ext))) {
    return `Solo se aceptan archivos ${extensions.join(", ")}.`;
  }
  if (file.size === 0) {
    return "El archivo está vacío.";
  }
  if (file.size > maxBytes) {
    return `El archivo pesa ${formatSize(file.size)} y el máximo es ${formatSize(maxBytes)}.`;
  }
  return null;
}

export function FileDropzone({
  file,
  onFileChange,
  accept = ".csv,.parquet",
  maxSizeMB = 512,
  disabled = false,
  className,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  accept?: string;
  maxSizeMB?: number;
  disabled?: boolean;
  className?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const maxBytes = maxSizeMB * 1024 * 1024;

  const accept_ = (candidate: File | undefined) => {
    setError(null);
    if (!candidate) return;
    const problem = validate(candidate, accept, maxBytes);
    if (problem) {
      setError(problem);
      onFileChange(null);
      return;
    }
    onFileChange(candidate);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (disabled) return;
    accept_(event.dataTransfer.files[0]);
  };

  const clear = () => {
    setError(null);
    onFileChange(null);
    // El <input type="file"> no se vacía al cambiar el estado de React: sin
    // esto, volver a elegir el MISMO archivo no dispara `change` y parece que
    // el botón no hiciera nada.
    if (input.current) input.current.value = "";
  };

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      <div
        onDragEnter={(event) => {
          event.preventDefault();
          if (!disabled) setDragging(true);
        }}
        onDragOver={(event) => event.preventDefault()}
        onDragLeave={(event) => {
          // `relatedTarget` fuera del contenedor: sin esta comprobación, pasar
          // por encima de un hijo dispara `dragleave` del padre y el resaltado
          // parpadea mientras se arrastra por adentro.
          if (!event.currentTarget.contains(event.relatedTarget as Node)) {
            setDragging(false);
          }
        }}
        onDrop={onDrop}
        className={cn(
          "relative flex min-h-36 flex-col items-center justify-center rounded-2xl border border-dashed px-6 py-8 text-center transition",
          dragging
            ? "border-accent/70 bg-accent/10"
            : "border-white/15 bg-white/[0.03] hover:border-white/25",
          disabled && "pointer-events-none opacity-50",
        )}
      >
        <input
          ref={input}
          type="file"
          accept={accept}
          disabled={disabled}
          onChange={(event) => accept_(event.target.files?.[0])}
          className="sr-only"
          aria-label="Elegir archivo"
        />

        {file ? (
          <>
            <div className="flex size-11 items-center justify-center rounded-full border border-accent/30 bg-accent/10">
              <FileSpreadsheet className="size-5 text-accent" />
            </div>
            <p className="mt-2.5 max-w-full truncate text-sm font-medium text-slate-100">
              {file.name}
            </p>
            <p className="tabular mt-0.5 text-xs text-slate-500">{formatSize(file.size)}</p>
            <button
              type="button"
              onClick={clear}
              aria-label="Quitar archivo"
              className="absolute right-3 top-3 flex size-7 items-center justify-center rounded-full bg-white/10 text-slate-300 transition hover:bg-white/20 hover:text-white"
            >
              <X className="size-3.5" />
            </button>
          </>
        ) : (
          <>
            <div className="flex size-11 items-center justify-center rounded-full border border-white/10 bg-white/5">
              <Upload className="size-5 text-slate-400" />
            </div>
            <p className="mt-2.5 text-sm font-medium text-slate-200">
              Arrastrá tu dataset acá
            </p>
            <p className="mt-0.5 text-xs text-slate-500">
              {accept.split(",").join(" o ")} · hasta {maxSizeMB} MB
            </p>
            <button
              type="button"
              onClick={() => input.current?.click()}
              className="mt-3 rounded-lg border border-white/15 px-3 py-1.5 text-xs text-slate-200 transition hover:border-accent/40 hover:text-accent"
            >
              o elegilo del disco
            </button>
          </>
        )}
      </div>

      {error && (
        <p
          role="alert"
          className="flex items-center gap-1.5 text-xs text-rose-300"
        >
          <AlertCircle className="size-3.5 shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

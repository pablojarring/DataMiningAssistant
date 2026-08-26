"use client";

// DataTable — tabla genérica con orden, filtro, paginación y visibilidad de
// columnas, sobre TanStack Table.
//
// Adaptada del componente "Complex Data Table" de 21st.dev. Del original se
// conserva lo que vale: el cableado de TanStack (los row models, el estado de
// orden/filtro/visibilidad, el render con `flexRender`). Lo que se descartó es
// la capa de presentación, que venía atada a las primitivas de shadcn y a sus
// tokens (`bg-primary`, `text-muted-foreground`, `bg-popover`) — tokens que
// esta app no define. Traerlos habría significado dos vocabularios de estilo
// conviviendo en la misma pantalla, y cinco paquetes de Radix para un menú
// desplegable y un checkbox que acá no se usan.
//
// Es genérica a propósito: la usan el listado de datasets y la vista de
// esquema, y las fases siguientes (pasos del pipeline, comparación de runs)
// también son tablas.

import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type Column,
  type ColumnDef,
  type ColumnFiltersState,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  Search,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/**
 * Cabecera que ordena al hacer clic.
 *
 * Se exporta como helper porque cada definición de columna la necesita, y
 * repetir el `onClick` con el `toggleSorting` en cada una es justo el tipo de
 * detalle que termina inconsistente entre columnas.
 */
export function SortableHeader<TData>({
  column,
  children,
  align = "left",
}: {
  column: Column<TData, unknown>;
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  const sorted = column.getIsSorted();
  const Icon = sorted === "asc" ? ArrowUp : sorted === "desc" ? ArrowDown : ChevronsUpDown;

  return (
    <button
      type="button"
      onClick={() => column.toggleSorting(sorted === "asc")}
      className={cn(
        "-mx-1.5 inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-[10px] uppercase tracking-wider transition hover:text-slate-200",
        sorted ? "text-accent" : "text-slate-500",
        align === "right" && "flex-row-reverse",
      )}
    >
      {children}
      <Icon className="size-3 shrink-0" />
    </button>
  );
}

function ColumnVisibilityMenu<TData>({
  table,
  labels,
}: {
  table: ReturnType<typeof useReactTable<TData>>;
  labels: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);

  // Cerrar al hacer clic afuera. Es la razón principal por la que un menú así
  // suele venir de una librería: sin esto queda abierto para siempre y tapa el
  // contenido. Con `pointerdown` y no `click`, para que cierre antes de que el
  // clic active lo que hay debajo.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const hideable = table.getAllColumns().filter((column) => column.getCanHide());
  if (hideable.length === 0) return null;

  return (
    <div className="relative" ref={container}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-slate-300 transition hover:border-accent/40 hover:text-slate-100"
      >
        <SlidersHorizontal className="size-3.5" />
        Columnas
      </button>

      {open && (
        <div className="glass absolute right-0 z-20 mt-1.5 min-w-44 rounded-xl p-1.5 shadow-xl shadow-black/40">
          {hideable.map((column) => (
            <label
              key={column.id}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-slate-300 transition hover:bg-white/10"
            >
              <input
                type="checkbox"
                checked={column.getIsVisible()}
                onChange={(event) => column.toggleVisibility(event.target.checked)}
                className="size-3.5 accent-[var(--color-accent)]"
              />
              {labels[column.id] ?? column.id}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

export function DataTable<TData>({
  data,
  columns,
  filterColumn,
  filterPlaceholder = "Filtrar…",
  columnLabels = {},
  pageSize = 10,
  emptyMessage = "Sin resultados.",
  rowLabel = "filas",
  onRowClick,
  isRowActive,
  className,
}: {
  data: TData[];
  columns: ColumnDef<TData, never>[];
  /** Columna sobre la que actúa la caja de búsqueda. Sin esto no se muestra. */
  filterColumn?: string;
  filterPlaceholder?: string;
  /** Nombres legibles por id de columna, para el menú de visibilidad. */
  columnLabels?: Record<string, string>;
  pageSize?: number;
  emptyMessage?: string;
  rowLabel?: string;
  /** Si se pasa, toda la fila es clicable. */
  onRowClick?: (row: TData) => void;
  /** Marca la fila seleccionada. */
  isRowActive?: (row: TData) => boolean;
  className?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({});

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnFilters, columnVisibility },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize } },
  });

  const filtered = table.getFilteredRowModel().rows.length;
  const showPagination = filtered > pageSize;

  return (
    <div className={cn("flex w-full flex-col gap-3", className)}>
      {(filterColumn || table.getAllColumns().some((c) => c.getCanHide())) && (
        <div className="flex flex-wrap items-center gap-2">
          {filterColumn && (
            <div className="relative min-w-52 flex-1 sm:max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-slate-500" />
              <input
                value={(table.getColumn(filterColumn)?.getFilterValue() as string) ?? ""}
                onChange={(event) =>
                  table.getColumn(filterColumn)?.setFilterValue(event.target.value)
                }
                placeholder={filterPlaceholder}
                className="w-full rounded-lg border border-white/10 bg-white/5 py-1.5 pl-8 pr-3 text-sm text-slate-100 placeholder:text-slate-500 focus:border-accent/50 focus:outline-none"
              />
            </div>
          )}
          <div className="ml-auto">
            <ColumnVisibilityMenu table={table} labels={columnLabels} />
          </div>
        </div>
      )}

      <div className="glass overflow-hidden rounded-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              {table.getHeaderGroups().map((headerGroup) => (
                <tr key={headerGroup.id} className="border-b border-white/10">
                  {headerGroup.headers.map((header) => (
                    <th
                      key={header.id}
                      // El estilo de cabecera vive acá y no en cada columna:
                      // así una cabecera de texto plano ("Formato") se ve igual
                      // que una ordenable, en vez de heredar el tamaño por
                      // defecto de la tabla y desentonar en la misma fila.
                      className="px-4 py-2.5 text-left text-[10px] font-medium uppercase tracking-wider text-slate-500"
                    >
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.length ? (
                table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.id}
                    // La fila responde a Enter y Espacio, no solo al clic: sin
                    // `role`/`tabIndex` una fila clicable es invisible para el
                    // teclado y para un lector de pantalla.
                    onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                    onKeyDown={
                      onRowClick
                        ? (event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              onRowClick(row.original);
                            }
                          }
                        : undefined
                    }
                    role={onRowClick ? "button" : undefined}
                    tabIndex={onRowClick ? 0 : undefined}
                    className={cn(
                      "border-t border-white/[0.06] transition",
                      onRowClick &&
                        "cursor-pointer hover:bg-white/[0.06] focus:bg-white/[0.06] focus:outline-none",
                      !onRowClick && "hover:bg-white/[0.04]",
                      isRowActive?.(row.original) && "bg-accent/10 hover:bg-accent/15",
                    )}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="px-4 py-2.5 align-middle">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))
              ) : (
                <tr>
                  <td
                    colSpan={table.getAllColumns().length}
                    className="px-4 py-8 text-center text-sm text-slate-500"
                  >
                    {emptyMessage}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showPagination && (
        <div className="flex items-center justify-between gap-4 text-xs text-slate-500">
          <span className="tabular">
            {filtered.toLocaleString()} {rowLabel} · página{" "}
            {table.getState().pagination.pageIndex + 1} de {table.getPageCount()}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              aria-label="Página anterior"
              className="rounded-lg border border-white/10 p-1.5 transition hover:border-accent/40 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronLeft className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              aria-label="Página siguiente"
              className="rounded-lg border border-white/10 p-1.5 transition hover:border-accent/40 hover:text-slate-200 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronRight className="size-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

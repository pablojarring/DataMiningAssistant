import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Une clases de Tailwind resolviendo los conflictos.
 *
 * Es la convención de shadcn y hace falta por algo concreto: en CSS gana la
 * clase que aparece última en la hoja de estilos, no la última del atributo.
 * Escribir `class="p-2 p-6"` deja el resultado a merced del orden en que
 * Tailwind generó las reglas. `twMerge` descarta la anterior y deja `p-6`, que
 * es lo que uno espera al pasar una clase para pisar un default del componente.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

import type { LucideIcon } from "lucide-react";
import { Inbox, WifiOff } from "lucide-react";

// Estados vacios/error consistentes del rediseño -- nunca un simple
// "Sin resultados" en texto plano suelto.
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-surface-border bg-card/40 px-6 py-12 text-center">
      <Icon className="h-8 w-8 text-slate-600" />
      <div className="text-sm font-medium text-slate-300">{title}</div>
      {description && <p className="max-w-sm text-xs text-slate-500">{description}</p>}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-red-500/20 bg-red-500/5 px-6 py-12 text-center">
      <WifiOff className="h-8 w-8 text-red-400" />
      <div className="text-sm font-medium text-red-300">No se pudo conectar con la API</div>
      <p className="max-w-sm text-xs text-red-400/70">{message}</p>
    </div>
  );
}

// Skeleton loaders genericos del rediseño (pedido explicito: "skeleton
// loaders mientras cargan los partidos"). `pulse` de Tailwind + los
// tokens de superficie nuevos -- se usan en vez de "Cargando..." como
// texto en cualquier sitio que muestre tarjetas o listas.
export function MatchCardSkeleton() {
  return (
    <div className="flex flex-col gap-4 rounded-xl border border-surface-border bg-card p-4">
      <div className="flex items-center justify-between">
        <div className="h-3 w-24 animate-pulse rounded bg-surface-alt" />
        <div className="h-3 w-20 animate-pulse rounded bg-surface-alt" />
      </div>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2.5">
          <div className="h-9 w-9 animate-pulse rounded-full bg-surface-alt" />
          <div className="h-3 w-24 animate-pulse rounded bg-surface-alt" />
        </div>
        <div className="flex items-center gap-2.5">
          <div className="h-3 w-24 animate-pulse rounded bg-surface-alt" />
          <div className="h-9 w-9 animate-pulse rounded-full bg-surface-alt" />
        </div>
      </div>
      <div className="h-14 animate-pulse rounded-lg bg-surface-alt/60" />
      <div className="flex items-center justify-between">
        <div className="h-4 w-28 animate-pulse rounded bg-surface-alt" />
        <div className="h-4 w-16 animate-pulse rounded bg-surface-alt" />
      </div>
    </div>
  );
}

export function MatchCardSkeletonGrid({ count = 6 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <MatchCardSkeleton key={i} />
      ))}
    </div>
  );
}

export function StatTileSkeleton() {
  return (
    <div className="rounded-xl border border-surface-border bg-card p-4">
      <div className="h-3 w-20 animate-pulse rounded bg-surface-alt" />
      <div className="mt-3 h-6 w-16 animate-pulse rounded bg-surface-alt" />
    </div>
  );
}

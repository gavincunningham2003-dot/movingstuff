import clsx from 'clsx';
import { useMemo, useState, type ReactNode } from 'react';

export interface Column<T> {
  /** Unique key; also used as the row property when `value` is omitted. */
  key: string;
  header: ReactNode;
  /** Raw value used for sorting and (when `render` is absent) display. */
  value?: (row: T) => string | number | null | undefined;
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right' | 'center';
  sortable?: boolean;
  className?: string;
  headerClassName?: string;
  width?: string;
}

export type SortDir = 'asc' | 'desc';

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, index: number) => string | number;
  initialSort?: { key: string; dir: SortDir };
  onRowClick?: (row: T) => void;
  rowClassName?: (row: T) => string | undefined;
  dense?: boolean;
  emptyMessage?: ReactNode;
  /** Max height with sticky header; omit for natural height. */
  maxHeight?: string;
  className?: string;
}

function rawValue<T>(col: Column<T>, row: T): string | number | null | undefined {
  if (col.value) return col.value(row);
  const v = (row as Record<string, unknown>)[col.key];
  if (v === null || v === undefined) return v as null | undefined;
  if (typeof v === 'number' || typeof v === 'string') return v;
  if (typeof v === 'boolean') return v ? 1 : 0;
  return String(v);
}

function compare(a: string | number | null | undefined, b: string | number | null | undefined): number {
  const aNull = a === null || a === undefined || a === '';
  const bNull = b === null || b === undefined || b === '';
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  initialSort,
  onRowClick,
  rowClassName,
  dense,
  emptyMessage = 'No rows.',
  maxHeight,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(initialSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const withIndex = rows.map((row, i) => ({ row, i, v: rawValue(col, row) }));
    withIndex.sort((x, y) => {
      const c = compare(x.v, y.v);
      if (c !== 0) return sort.dir === 'asc' ? c : -c;
      return x.i - y.i;
    });
    return withIndex.map((w) => w.row);
  }, [rows, sort, columns]);

  function toggleSort(col: Column<T>) {
    if (col.sortable === false) return;
    setSort((prev) => {
      if (!prev || prev.key !== col.key) return { key: col.key, dir: 'asc' };
      if (prev.dir === 'asc') return { key: col.key, dir: 'desc' };
      return null;
    });
  }

  const cellPad = dense ? 'px-3 py-1.5' : 'px-3 py-2';

  return (
    <div className={clsx('overflow-auto', className)} style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full min-w-max border-collapse text-sm">
        <thead className="sticky top-0 z-10 bg-slate-900">
          <tr className="border-b border-slate-800">
            {columns.map((col) => {
              const active = sort?.key === col.key;
              const sortable = col.sortable !== false;
              return (
                <th
                  key={col.key}
                  scope="col"
                  style={col.width ? { width: col.width } : undefined}
                  onClick={() => toggleSort(col)}
                  className={clsx(
                    cellPad,
                    'whitespace-nowrap text-xs font-semibold uppercase tracking-wide text-slate-400',
                    col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left',
                    sortable && 'cursor-pointer select-none hover:text-slate-200',
                    active && 'text-teal-300',
                    col.headerClassName,
                  )}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {sortable && (
                      <span className="text-[10px] text-slate-500">
                        {active ? (sort?.dir === 'asc' ? '▲' : '▼') : '↕'}
                      </span>
                    )}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="px-3 py-6 text-center text-sm text-slate-500">
                {emptyMessage}
              </td>
            </tr>
          )}
          {sorted.map((row, i) => (
            <tr
              key={rowKey(row, i)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={clsx(
                'border-b border-slate-800/70 last:border-b-0',
                onRowClick && 'cursor-pointer hover:bg-slate-800/60',
                rowClassName?.(row),
              )}
            >
              {columns.map((col) => {
                const v = rawValue(col, row);
                const content = col.render ? col.render(row) : v === null || v === undefined || v === '' ? '—' : v;
                return (
                  <td
                    key={col.key}
                    className={clsx(
                      cellPad,
                      'tabular align-middle text-slate-200',
                      col.align === 'right' ? 'text-right' : col.align === 'center' ? 'text-center' : 'text-left',
                      col.className,
                    )}
                  >
                    {content}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

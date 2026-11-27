export type Config = Record<string, string | number | boolean | null>;
export interface Result {
  task_id: string; title: string; output_path: string; status: string; reason: string;
  page_count: number; completed_pages: number[]; cost_usd: number; retry_at: number | null;
  paid_eligible: boolean; revision_id: number; exported_revision_id: number; published_revision_id: number;
  notion: { page_id: string; url: string; mode: string } | null;
}
export interface Estimate {
  document: { page_count: number }; cached_pages: number; pending_pages: number; planned_requests: number;
  quota: { remaining_requests: number | null } | null; quota_note: string;
  cost_low: number | null; cost_high: number | null; reservation: number | null;
  price_note: string; backup_enabled: boolean; backup_warning: string; cost_model: string;
}
export interface State {
  ready: boolean; busy: boolean; closing: boolean; message: string; file: string; markdown: string;
  mode: 'create' | 'resume' | 'open' | 'export' | 'reconcile'; config: Config;
  credentials: Record<string, { saved: boolean; environment: boolean; unavailable: boolean }>;
  result: Result | null; estimate: Estimate | null;
  events: { time: number; message: string }[]; operation: { id: number; error: string | null };
}
export interface Model { id: string; name: string; free: boolean; conditional_free?: boolean;
  input_price?: number; output_price?: number; image_price?: number; request_price?: number; }
export interface CacheRow { id: string; document: string; updated: number; status: string; page_count: number;
  cache_key: string; body_bytes: number; shared_bytes: number; shared_tasks: number; protected_reason: string; detail?: string; }
export interface Storage { cache_bytes: number; reclaimable_bytes: number; tasks: CacheRow[]; }
export interface Started { started: boolean; operation_id: number; }

declare global {
  interface Window { pywebview?: { api: { get_state(): Promise<State>; call(name: string, args?: object): Promise<unknown> } }; }
}
export async function api<T = unknown>(name: string, args: object = {}): Promise<T> {
  if (!window.pywebview) throw new Error('Open NoteAgent with its desktop launcher.');
  const result = await window.pywebview.api.call(name, args);
  if (result && typeof result === 'object' && 'error' in result) throw new Error(String(result.error));
  return result as T;
}
export const basename = (path: string) => path.split(/[\\/]/).pop() || path;
export const money = (value: number) => `$${value.toFixed(value < .01 ? 4 : 2)}`;
export function bytes(value: number): string {
  for (const unit of ['B', 'KB', 'MB', 'GB']) { if (value < 1024 || unit === 'GB') return `${unit === 'B' ? value : value.toFixed(1)} ${unit}`; value /= 1024; }
  return '';
}
export function cacheSelection(rows: CacheRow[], selected: Set<string>) {
  let size = 0; let sharedKept = false;
  const groups = new Map<string, CacheRow[]>();
  for (const row of rows) { groups.set(row.cache_key, [...(groups.get(row.cache_key) || []), row]); if (selected.has(row.id) && !row.protected_reason) size += row.body_bytes; }
  for (const members of groups.values()) {
    if (!members.some(row => selected.has(row.id)) || !members[0].shared_bytes) continue;
    if (members.every(row => selected.has(row.id) && !row.protected_reason)) size += members[0].shared_bytes;
    else sharedKept = true;
  }
  return { size, sharedKept };
}

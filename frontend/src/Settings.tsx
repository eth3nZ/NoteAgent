import { useEffect, useMemo, useState } from 'react';
import { Check, ExternalLink, LoaderCircle, RefreshCw, Trash2, X } from 'lucide-react';
import { api, bytes, cacheSelection, money, type CacheRow, type Config, type Model, type Started, type State, type Storage } from './api';

export function Modal({ title, children, onClose, wide = false }: { title: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  return <div className="modal-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
      <header className="modal-title"><h2>{title}</h2><button className="icon" aria-label="Close dialog" onClick={onClose}><X size={20} /></button></header>
      {children}
    </section>
  </div>;
}

function StoragePanel({ state, report }: { state: State; report: (message: string) => void }) {
  const [info, setInfo] = useState<Storage | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [clearing, setClearing] = useState<number | null>(null);
  const reload = async () => { setLoading(true); try { const value = await api<Storage>('storage'); setInfo(value); setSelected(new Set()); setError(''); } catch (e) { setError(String(e)); } finally { setLoading(false); } };
  useEffect(() => { if (!state.busy) void reload(); }, [state.busy]);
  useEffect(() => { if (clearing && clearing > 0 && state.operation.id >= clearing && !state.busy) { setClearing(null); void reload(); } }, [clearing, state.operation, state.busy]);
  const rows = info?.tasks || [];
  const available = rows.filter(row => !row.protected_reason);
  const estimate = cacheSelection(rows, selected);
  const toggle = (row: CacheRow) => setSelected(old => { const next = new Set(old); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next; });
  return <div className="storage-panel">
    <div className="section-intro"><h3>Local cache</h3><p>Clear completed tasks. Exported notes and Notion pages are kept.</p></div>
    <div className="storage-top"><strong>{info ? bytes(info.cache_bytes) : '—'} cached</strong>
      <button className="icon" aria-label="Refresh cache list" disabled={loading || state.busy} onClick={() => void reload()}><RefreshCw size={17} className={loading ? 'spin' : ''} /></button></div>
    <div className="cache-scroll"><table className="cache-table"><thead><tr><th><input type="checkbox" aria-label="Select available caches" disabled={state.busy || !available.length}
      checked={!!available.length && selected.size === available.length} onChange={event => setSelected(new Set(event.target.checked ? available.map(row => row.id) : []))} /></th>
      <th>Document</th><th>Last used</th><th>Pages</th><th>Status</th><th>Cache</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id} title={`${row.detail || ''} · ${row.shared_tasks} task(s) share page data`}>
        <td><input type="checkbox" aria-label={`Select ${row.document}`} checked={selected.has(row.id)} disabled={state.busy || !!row.protected_reason} onChange={() => toggle(row)} /></td>
        <td>{row.document}</td><td>{new Date(row.updated * 1000).toLocaleDateString()}</td><td>{row.page_count}</td>
        <td title={row.protected_reason}>{row.protected_reason || 'Completed'}</td><td>{bytes(row.body_bytes + row.shared_bytes)}</td>
      </tr>)}</tbody></table>
      {!rows.length && !loading && <div className="quiet-empty"><Check size={24} /><p>No cached tasks.</p><span>Your exported notes and fee history remain saved.</span></div>}
    </div>
    <div className="storage-footer"><div><strong>{selected.size ? `${selected.size} selected · about ${bytes(estimate.size)}` : 'Select tasks to clear'}</strong>
      <p>{estimate.sharedKept ? 'Shared pages are kept for unselected tasks.' : 'Shared page data is counted once.'}</p></div>
      <button className="button danger-subtle" disabled={state.busy || clearing !== null || !estimate.size} onClick={() => { setClearing(-1); void api<Started>('clear_cache', { task_ids: [...selected] }).then(value => setClearing(value.operation_id)).catch(e => { setClearing(null); report(String(e)); }); }}>Clear selected cache</button></div>
    <p className="caption">Regenerating cleared notes may use model requests again. Unfinished tasks, pending retries, revisions and unresolved charges are protected.</p>
    {error && <p role="alert" className="inline-error">{error}</p>}
  </div>;
}

export function Settings({ state, onClose, report }: { state: State; onClose: () => void; report: (message: string) => void }) {
  const [tab, setTab] = useState<'models' | 'notion' | 'storage'>('models');
  const [draft, setDraft] = useState<Config>({ ...state.config });
  const [secrets, setSecrets] = useState<Record<string, { action: string; value?: string }>>({});
  const [models, setModels] = useState<Model[]>([]);
  const [search, setSearch] = useState('');
  const [loadingModels, setLoadingModels] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState<number | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const service = String(draft.provider || 'openrouter');
  const modelField = service === 'gemini' ? 'gemini_model' : 'vision_model';
  const keyField = service === 'gemini' ? 'gemini_api_key' : 'api_key';
  const set = (key: string, value: Config[string]) => setDraft(old => ({ ...old, [key]: value }));
  useEffect(() => {
    if (saving && state.operation.id === saving && !state.busy) {
      if (state.operation.error) { setNote(state.operation.error); setSaving(null); }
      else onClose();
    }
  }, [state.operation, state.busy, saving, onClose]);
  const loadModels = async () => {
    setLoadingModels(true); setNote('');
    try { setModels(await api<Model[]>('models', { service })); }
    catch (e) { setNote(service === 'gemini' ? `Save and confirm your Google connection before loading its model list. ${String(e)}` : String(e)); }
    finally { setLoadingModels(false); }
  };
  useEffect(() => { setModels([]); setSearch(''); if (service === 'openrouter' || (service === 'gemini' && state.config.gemini_free_project)) void loadModels(); }, [service]);
  const filtered = useMemo(() => models.filter(model => `${model.name} ${model.id}`.toLowerCase().includes(search.toLowerCase())), [models, search]);
  const changeSecret = (key: string, value: string, clear = false) => {
    setSecrets(old => ({ ...old, [key]: { action: clear ? 'clear' : 'replace', value } }));
    if (key === 'gemini_api_key') set('gemini_free_project', false);
  };
  const secret = (key: string, label: string, placeholder?: string) => <label className="field">{label}<div className="secret-input">
    <input aria-label={label} type="password" autoComplete="off" value={secrets[key]?.value || ''} placeholder={placeholder || (state.credentials[key]?.unavailable ? 'Unavailable · unlock Keychain or replace' : state.credentials[key]?.saved ? 'Saved in Keychain' : state.credentials[key]?.environment ? 'Using environment variable' : 'Paste your key')}
      onChange={event => changeSecret(key, event.target.value)} />
    <button className="icon" aria-label={`Clear ${label}`} title="Clear this saved credential on Save" onClick={() => changeSecret(key, '', true)}><Trash2 size={16} /></button></div>
    {secrets[key]?.action === 'clear' && <span className="caption">Will be cleared when you save.</span>}</label>;
  const selectModel = (id: string, backup = false) => {
    const model = models.find(item => item.id === id);
    setDraft(old => ({ ...old, [backup ? 'paid_model' : modelField]: id,
      ...(!backup && service === 'openrouter' ? { primary_paid: !!model && !model.free, free_providers: '' } : {}),
      ...(model && !model.free ? { input_price: model.input_price || 0, output_price: model.output_price || 0,
        paid_image_price: model.image_price || 0, paid_request_price: model.request_price || 0 } : {}) }));
  };
  const modelChoice = (backup = false) => {
    const value = String(draft[backup ? 'paid_model' : modelField] || '');
    const choices = backup ? filtered.filter(model => !model.free) : filtered;
    return <select aria-label={backup ? 'Backup model' : 'Generation model'} value={value} onChange={event => selectModel(event.target.value, backup)}>
      {!choices.some(model => model.id === value) && <option value={value}>{value || 'Choose a model'}</option>}
      {choices.map(model => <option key={model.id} value={model.id}>{model.name} · {model.conditional_free ? 'Google project quota' : model.free ? 'Free' : `$${model.input_price} / $${model.output_price} per 1M`}</option>)}</select>;
  };
  const save = async () => {
    setNote('');
    try { const result = await api<Started>('save_settings', { values: draft, secrets, confirm_gemini_free: !!draft.gemini_free_project }); setSaving(result.operation_id); }
    catch (e) { setNote(String(e)); }
  };
  const requestClose = () => { if (saving || state.busy) setNote('Finishing the current operation…'); else onClose(); };
  return <Modal title="Settings" onClose={requestClose} wide>
    <nav className="settings-tabs">{(['models', 'notion', 'storage'] as const).map(name => <button key={name} className={tab === name ? 'active' : ''} onClick={() => setTab(name)}>{name[0].toUpperCase() + name.slice(1)}</button>)}</nav>
    <div className="modal-body settings-body">
      {tab === 'storage' ? <StoragePanel state={state} report={report} /> : tab === 'notion' ? <>
        <div className="section-intro"><h3>Connect your Notion page</h3><p>Create an internal connection, then give it access to your destination page.</p></div>
        {secret('notion_token', 'Integration token')}
        <label className="field">Destination page URL<input value={String(draft.notion_page_id || '')} placeholder="https://www.notion.so/…" onChange={event => set('notion_page_id', event.target.value)} /></label>
        <div className="help-card"><strong>First time connecting?</strong><ol><li>Create an internal connection in the Notion developer portal.</li><li>On your destination page, choose ••• → Connections and add it.</li><li>Paste its token and page URL above, then Save.</li></ol>
          <button className="text-button" onClick={() => void api('open_link', { url: 'https://app.notion.com/developers/connections' })}>Open Notion connections <ExternalLink size={13} /></button></div>
      </> : <>
        <div className="section-intro"><h3>Your model connection</h3><p>Page text and images go to your selected cloud service. Source files and saved progress stay local.</p></div>
        <label className="field">Service<select aria-label="Service" value={service} onChange={event => setDraft(old => ({...old, provider:event.target.value,
          primary_paid:event.target.value === 'openrouter' && !String(old.vision_model || '').endsWith(':free')}))}>
          <option value="openrouter">OpenRouter</option><option value="gemini">Google Gemini · unbilled project</option><option value="custom">Custom compatible endpoint</option></select></label>
        {secret(keyField, service === 'gemini' ? 'Gemini API key' : service === 'openrouter' ? 'OpenRouter API key' : 'API key')}
        {service === 'gemini' && <div className="help-card"><label className="check"><input type="checkbox" checked={!!draft.gemini_free_project} onChange={event => set('gemini_free_project', event.target.checked)} />I confirm this key's Google project has no billing enabled.</label>
          <p>This is your confirmation, not a server-verified tier. Check the project's billing page. Quotas and regional availability are managed by Google. Replacing the key requires confirmation again.</p>
          <button className="text-button" onClick={() => void api('open_link', { url: 'https://aistudio.google.com/projects' })}>Check Google project <ExternalLink size={13} /></button></div>}
        {service === 'custom' && <label className="field">Base URL<input value={String(draft.base_url || '')} onChange={event => { set('base_url', event.target.value); changeSecret('api_key', '', true); }} placeholder="https://provider.example/v1" /></label>}
        {service !== 'custom' && <div className="model-search"><input aria-label="Search models" placeholder="Search models…" value={search} onChange={event => setSearch(event.target.value)} /><button className="icon" aria-label="Refresh models" disabled={loadingModels} onClick={() => void loadModels()}><RefreshCw size={17} className={loadingModels ? 'spin' : ''} /></button></div>}
        <label className="field">Generation model{service === 'custom' ? <input value={String(draft.vision_model || '')} onChange={event => set('vision_model', event.target.value)} placeholder="Your provider's model ID" /> : modelChoice()}</label>
        {service === 'openrouter' && !!draft.primary_paid && <p className="inline-note">Paid model selected. Starting generation requires an explicit paid action and must fit both budgets.</p>}
        {service !== 'custom' && !draft.primary_paid && <label className="check"><input type="checkbox" checked={!!draft.paid_enabled} onChange={event => set('paid_enabled', event.target.checked)} />Configure an OpenRouter paid backup</label>}
        {!draft.primary_paid && !!draft.paid_enabled && <div className="inset">
          {secret('paid_api_key', 'Backup OpenRouter key', 'Empty uses your saved OpenRouter key')}
          <label className="field">Backup model{service === 'openrouter' ? modelChoice(true) : <input value={String(draft.paid_model || '')} onChange={event => set('paid_model', event.target.value)} />}</label>
          <p className="caption">Only offered after the next day's free retry fails. Every paid continuation needs your action.</p></div>}
        <div className="field-row">{[['document_limit', 'Per document'], ['monthly_limit', 'Per month (UTC)']].map(([key, label]) => <label className="field" key={key}>{label}<div className="amount"><span>$</span><input type="number" min="0" step="0.01" value={Number(draft[key] || 0)} onChange={event => set(key, Number(event.target.value))} /></div></label>)}</div>
        <details open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}><summary>Advanced connection options</summary><div className="advanced-fields">
          {service !== 'custom' && <label className="field">Manual model ID<input value={String(draft[modelField] || '')} onChange={event => { set(modelField, event.target.value); if (service === 'openrouter') set('primary_paid', !event.target.value.endsWith(':free')); }} /></label>}
          {service === 'openrouter' && <label className="field">Allowed hosts<input value={String(draft.free_providers || '')} onChange={event => set('free_providers', event.target.value)} placeholder="Automatic" /></label>}
          <div className="field-row">{[['input_price', 'Input ceiling / 1M'], ['output_price', 'Output ceiling / 1M']].map(([key, label]) => <label className="field" key={key}>{label}<input type="number" min="0" step="0.001" value={Number(draft[key] || 0)} onChange={event => set(key, Number(event.target.value))} /></label>)}</div>
        </div></details>
        <button className="button" disabled={state.busy} onClick={() => void api('test_connection').then(() => setNote('Testing the saved free connection…')).catch(e => setNote(String(e)))}>Test saved free connection</button>
        <p className="caption">Save changes before testing. The image test uses a free request; paid models are never used for this test.</p>
      </>}
      {note && <p role="alert" className="inline-note">{note}</p>}
    </div>
    <footer className="modal-actions"><span className="caption">{state.busy ? state.message : 'Keys are stored in macOS Keychain.'}</span>
      <button className="button" onClick={requestClose} disabled={!!saving}>Close</button>
      <button className="button primary" onClick={() => void save()} disabled={state.busy || !!saving}>{saving ? <LoaderCircle size={16} className="spin" /> : null}Save settings</button></footer>
  </Modal>;
}

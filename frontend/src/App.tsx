import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, BookOpen, Braces, Check, ChevronDown, Clock3, FileText, FolderOpen, List, LoaderCircle, MoreHorizontal, PanelRightOpen, RefreshCw, Settings2, Square, Upload, X } from 'lucide-react';
import { api, basename, money, type Config, type Started, type State } from './api';
import { Reader, type Heading } from './Reader';
import { Modal, Settings } from './Settings';
import logo from '../../app/assets/noteagent-rounded.png';

function Source({ state, onClose }: { state: State; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<{ image: string; note: string } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const revision = useRef(0);
  useEffect(() => {
    let active = true; const version = ++revision.current;
    setLoading(true); setError(''); setData(null);
    void api<{ image: string; note: string }>('source', { page }).then(value => { if (active && version === revision.current) setData(value); }).catch(e => { if (active) setError(String(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, state.result?.task_id]);
  useEffect(() => () => { void api('source_close'); }, []);
  return <aside className="source-panel" aria-label="Source review"><header><div><span className="eyebrow">SOURCE REVIEW</span><h3>Check the original</h3></div>
    <button className="icon" aria-label="Close source review" onClick={onClose}><X size={18} /></button></header>
    <div className="source-pager"><button className="button" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>Previous</button><label>Page <input type="number" min="1" max={state.result?.page_count || 1} value={page} onChange={event => setPage(Math.max(1, Math.min(Number(event.target.value) || 1, state.result?.page_count || 1)))} /> / {state.result?.page_count}</label><button className="button" disabled={page >= (state.result?.page_count || 1)} onClick={() => setPage(value => value + 1)}>Next</button></div>
    <div className="source-scroll">{loading && <div className="source-loading"><LoaderCircle className="spin" size={20} />Rendering locally…</div>}
      {error && <div className="inline-error"><p>{error}</p><button className="button" onClick={() => void api<{ image: string; note: string }>('locate_source', { page }).then(value => { if (value) { setData(value); setError(''); } }).catch(e => setError(String(e)))}>Locate original source</button></div>}
      {data && <><img className="source-image" src={data.image} alt={`Original source page ${page}`} /><div className="source-excerpt"><span className="eyebrow">ACCEPTED PAGE NOTE</span><Reader markdown={data.note} /></div></>}
    </div></aside>;
}

function Revision({ state, onClose, report }: { state: State; onClose: () => void; report: (message: string) => void }) {
  const [text, setText] = useState(state.markdown);
  const [preview, setPreview] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [operation, setOperation] = useState<number | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { if (operation && state.operation.id === operation && !state.busy) {
    if (state.operation.error) { setError(state.operation.error); setOperation(null); } else onClose();
  } }, [operation, state.operation, state.busy, onClose]);
  const save = async () => { try { const value = await api<Started>('revise', { markdown: text }); setOperation(value.operation_id); } catch (e) { setError(String(e)); report(String(e)); } };
  const close = () => { if (state.busy) return; if (text !== state.markdown) setConfirmClose(true); else onClose(); };
  return <Modal title="Edit notes" wide onClose={close}>
    <div className="editor-toolbar"><span className="caption">Revisions keep the original generated notes. Export or publish after saving.</span><button className="button" onClick={() => setPreview(value => !value)}>{preview ? 'Edit Markdown' : 'Preview'}</button></div>
    <div className="editor-content">{preview ? <Reader markdown={text} /> : <textarea aria-label="Markdown revision" spellCheck={false} value={text} onChange={event => setText(event.target.value)} />}</div>
    {error && <p role="alert" className="inline-error">{error}</p>}
    <footer className="modal-actions">{confirmClose ? <><span>Unsaved changes</span><button className="button" onClick={() => setConfirmClose(false)}>Keep editing</button><button className="button" onClick={onClose}>Discard</button></> : <><span className="caption">Your code and whitespace are kept as entered.</span><button className="button" onClick={close}>Close</button></>}
      <button className="button primary" disabled={state.busy || !text.trim()} onClick={() => void save()}>Save revision</button></footer>
  </Modal>;
}

function Reconcile({ state, onClose, report }: { state: State; onClose: () => void; report: (message: string) => void }) {
  const [rows, setRows] = useState<{ id: string; model: string; reserved_usd: number }[]>([]);
  const [amount, setAmount] = useState('0');
  useEffect(() => { if (!state.busy) void api<typeof rows>('uncertain_requests').then(setRows).catch(e => report(String(e))); }, [state.busy]);
  return <Modal title="Reconcile charge" onClose={onClose}><div className="modal-body"><p>Check the provider dashboard before entering the actual charge. An unknown charge stays reserved until you verify it.</p>
    {rows.length ? <><div className="help-card"><strong>{rows[0].model}</strong><p>Reserved: {money(rows[0].reserved_usd)}</p></div><label className="field">Verified actual charge ($)<input type="number" min="0" step="0.000001" value={amount} onChange={event => setAmount(event.target.value)} /></label></> : <p>No unresolved requests remain.</p>}</div>
    <footer className="modal-actions"><button className="button" onClick={onClose}>Close</button><button className="button primary" disabled={state.busy || !rows.length} onClick={() => void api('reconcile', { request_id: rows[0].id, amount: Number(amount) }).catch(e => report(String(e)))}>Confirm verified amount</button></footer></Modal>;
}

const initial: State = { ready: false, busy: false, closing: false, file: '', markdown: '', result: null, estimate: null, config: {}, credentials: {}, mode: 'create', message: 'Opening your workspace…', events: [], operation: { id: 0, error: null } };

export default function App() {
  const [state, setState] = useState<State>(initial);
  const [notice, setNotice] = useState('');
  const [settings, setSettings] = useState(false);
  const [revision, setRevision] = useState(false);
  const [source, setSource] = useState(false);
  const [reconcile, setReconcile] = useState(false);
  const [publishConfirm, setPublishConfirm] = useState(false);
  const [paidConfirm, setPaidConfirm] = useState(false);
  const [toc, setToc] = useState(true);
  const [headings, setHeadings] = useState<Heading[]>([]);
  const [more, setMore] = useState(false);
  const [options, setOptions] = useState(false);
  const [activity, setActivity] = useState(false);
  const [raw, setRaw] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [acting, setActing] = useState(false);
  const [pendingOperation, setPendingOperation] = useState<number | null>(null);
  const [dragging, setDragging] = useState(false);
  const reader = useRef<HTMLDivElement>(null);
  const lastEstimate = useRef('');
  const config = state.config;
  const result = state.result;
  const hasNotes = !!(result?.completed_pages.length || result?.revision_id);
  const busy = state.busy || acting;
  const onHeadings = useCallback((items: Heading[]) => setHeadings(items), []);
  useEffect(() => {
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try { if (typeof window.pywebview?.api?.get_state === 'function') {
        const value = await window.pywebview.api.get_state();
        if (!stopped) { setState(value); setNotice(old => old === 'The desktop connection is unavailable. Saved notes remain on disk.' ? '' : old); }
      } }
      catch { if (!stopped) setNotice('The desktop connection is unavailable. Saved notes remain on disk.'); }
      if (!stopped) timer = setTimeout(poll, 250);
    };
    void poll(); return () => { stopped = true; clearTimeout(timer); };
  }, []);
  const refresh = async (force = true) => { setRefreshing(true); try { await api('estimate', { refresh: force }); } catch (e) { setNotice(String(e)); } finally { setRefreshing(false); } };
  useEffect(() => {
    const identity = JSON.stringify([state.file, config.provider, config.vision_model, config.gemini_model, config.primary_paid, config.language, config.paid_model, config.paid_enabled, config.input_price, config.output_price]);
    if (state.ready && state.file && identity !== lastEstimate.current && !state.busy) { lastEstimate.current = identity; void refresh(false); }
  }, [state.ready, state.file, config, state.busy]);
  useEffect(() => { if (state.estimate === null && !state.busy) lastEstimate.current = ''; }, [state.estimate, state.busy]);
  useEffect(() => { if (state.ready) void api('interaction', { active: settings || revision || source || reconcile || publishConfirm || paidConfirm || more }); }, [settings, revision, source, reconcile, publishConfirm, paidConfirm, more, state.ready]);
  useEffect(() => { if (result?.status === 'completed' && !hasNotes && !state.markdown) setSource(false); }, [result?.status, hasNotes, state.markdown]);
  const closeSettings = useCallback(() => setSettings(false), []);
  const closeRevision = useCallback(() => setRevision(false), []);
  const closeSource = useCallback(() => setSource(false), []);
  const closeReconcile = useCallback(() => setReconcile(false), []);
  const preferences = async (values: Config) => { try { await api('preferences', values); } catch (e) { setNotice(String(e)); } };
  useEffect(() => { if (pendingOperation && state.operation.id >= pendingOperation) { setActing(false); setPendingOperation(null); } }, [pendingOperation, state.operation]);
  const perform = async (name: string, args: object = {}) => {
    setActing(true); setNotice(''); setMore(false);
    try { const value = await api<Started | null>(name, args); if (value?.started) setPendingOperation(value.operation_id); else setActing(false); }
    catch (e) { setNotice(String(e)); setActing(false); }
  };
  const primary = () => {
    if (state.mode === 'open') void perform('open_output');
    else if (state.mode === 'export') void perform('export');
    else if (state.mode === 'reconcile') setReconcile(true);
    else void perform('start', { resume: state.mode === 'resume', allow_paid_primary: !!config.primary_paid && config.provider === 'openrouter' });
  };
  const paidPrimary = !!config.primary_paid && config.provider === 'openrouter';
  const waiting = !!(state.mode === 'resume' && result?.status === 'waiting_quota' && result.retry_at && result.retry_at * 1000 > Date.now());
  const labels = { create: paidPrimary ? 'Create notes · paid' : 'Create notes', resume: paidPrimary ? 'Resume · paid' : 'Resume', open: 'Open notes', export: 'Export notes', reconcile: 'Reconcile charge' };
  const model = String(config.provider === 'gemini' ? config.gemini_model || 'Gemini' : config.vision_model || 'No model selected');
  const completed = result?.completed_pages.length || 0;
  const pending = state.estimate?.pending_pages;
  const needsPublishConfirm = !!(result && (result.reason === 'notion_failed' || (result.notion && result.revision_id !== result.published_revision_id)));
  return <main className="app-shell" onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={event => { event.preventDefault(); setDragging(false); }}>
    <header className="app-header"><div className="brand"><img src={logo} alt="NA" /><div><strong>NoteAgent</strong><span>Course material, made clear.</span></div></div>
      <button className="button settings-button" onClick={() => setSettings(true)} disabled={!state.ready || state.busy}><Settings2 size={16} />Settings</button></header>
    <section className={`document-bar ${dragging ? 'dragging' : ''}`}>
      <button className="document-picker" onClick={() => void perform('pick_file')} disabled={busy || !state.ready}><span className="file-badge">{state.file ? <FileText size={23} /> : <Upload size={23} />}</span><span><strong>{state.file ? basename(state.file) : 'Choose your course material'}</strong><small>{state.file ? state.file.toLowerCase().endsWith('.pptx') ? 'PowerPoint document' : 'PDF document' : 'Drop a PDF or PowerPoint here, or browse'}</small></span><FolderOpen size={17} /></button>
      <div className="quick-options"><label><span>Language</span><select aria-label="Output language" disabled={busy} value={String(config.language || 'en')} onChange={event => void preferences({ language: event.target.value })}><option value="en">English</option><option value="zh">中文</option></select></label>
        <label><span>Export</span><select aria-label="Output format" disabled={busy} value={String(config.output_format || 'md')} onChange={event => void preferences({ output_format: event.target.value })}><option value="md">Markdown</option><option value="ipynb">Notebook</option></select></label>
        <button className={`icon ${options ? 'selected' : ''}`} aria-label="Note options" title="Save folder, source references and Notion" onClick={() => setOptions(value => !value)}><ChevronDown size={18} /></button></div>
    </section>
    {options && <section className="note-options"><div className="output-location"><FolderOpen size={15} /><span title={String(config.output_dir || '')}>{String(config.output_dir || '')}</span><button className="text-button" disabled={busy} onClick={() => void perform('pick_folder')}>Change</button></div>
      <div className="option-row"><label className="check"><input type="checkbox" disabled={busy} checked={!!config.include_source_pages} onChange={event => void preferences({ include_source_pages: event.target.checked })} />Include source pages</label>
      <label className="check"><input type="checkbox" disabled={busy} checked={!!config.publish_notion} onChange={event => void preferences({ publish_notion: event.target.checked })} />Publish to Notion</label>
      {!!config.publish_notion && <select aria-label="Notion mode" disabled={busy} value={String(config.notion_mode || 'create_child')} onChange={event => void preferences({ notion_mode: event.target.value })}><option value="create_child">Create a new note</option><option value="append">Append to page</option></select>}
      {!!config.publish_notion && <span className="caption">{config.notion_page_id ? 'Page configured' : 'Connect a page in Settings'}</span>}</div></section>}
    <section className="model-strip"><button className="model-chip" onClick={() => setSettings(true)} title={model}><span className="status-dot" />{config.provider === 'gemini' ? 'Gemini' : config.provider === 'custom' ? 'Custom' : 'OpenRouter'}<span className="divider">/</span><strong>{model.split('/').pop()}</strong><ChevronDown size={13} /></button>
      <span className="model-cost">{config.provider === 'gemini' ? config.gemini_free_project ? 'Unbilled Google project · user-confirmed' : 'Confirm your Google project in Settings' : paidPrimary ? `Paid · ${money(Number(config.document_limit || 0))} document cap` : config.provider === 'custom' ? 'Provider billing applies' : 'Free route · $0'}</span></section>
    {state.file && <section className="estimate-bar" aria-label="Document estimate"><div><span>DOCUMENT</span><strong>{state.estimate ? `${state.estimate.document.page_count} pages` : 'Checking…'}</strong><small>{state.estimate ? `${state.estimate.cached_pages} cached · ≈ ${state.estimate.planned_requests} requests` : 'Local scan only'}</small></div>
      <div title={state.estimate?.quota_note}><span>FREE ALLOWANCE</span><strong>{state.estimate?.quota?.remaining_requests != null ? `${state.estimate.quota.remaining_requests} requests` : 'Unknown'}</strong><small>{config.provider === 'gemini' ? 'Managed by Google project' : 'Shared usage and retries affect coverage'}</small></div>
      <div title={state.estimate?.price_note}><span>{paidPrimary ? 'ESTIMATED COST' : 'PAID BACKUP'}</span><strong>{state.estimate?.cost_low != null ? `${money(state.estimate.cost_low)}–${money(state.estimate.cost_high || 0)}` : (paidPrimary || config.paid_enabled) ? 'Price unavailable' : 'Off'}</strong><small>{pending === 0 ? 'No new model requests needed' : state.estimate?.reservation != null ? `Per-request reservation ${money(state.estimate.reservation)}` : 'Selected model pricing'}</small></div>
      <button className="icon" aria-label="Refresh estimate" title="Refresh allowance, prices and cached pages" disabled={refreshing || state.busy} onClick={() => void refresh()}><RefreshCw size={17} className={refreshing ? 'spin' : ''} /></button>
      {state.estimate?.backup_warning && <p className="estimate-warning">{state.estimate.backup_warning}</p>}</section>}
    <section className={`workspace ${source ? 'with-source' : ''}`}>
      {state.markdown ? <>
        <header className="reader-toolbar"><div className="reader-title"><BookOpen size={17} /><strong>{result?.title || 'Your notes'}</strong>{!!result?.revision_id && <span className="revision-badge">Revision</span>}</div>
          <div className="reader-tools"><button className={`icon ${toc ? 'selected' : ''}`} aria-label="Toggle contents" title="Contents" onClick={() => setToc(value => !value)}><List size={17} /></button>
            <button className={`icon ${raw ? 'selected' : ''}`} aria-label="Toggle Markdown" title="Markdown" onClick={() => setRaw(value => !value)}><Braces size={17} /></button>
            <button className={`icon ${source ? 'selected' : ''}`} aria-label="Review sources" title="Review original pages" disabled={state.busy || !result} onClick={() => setSource(value => !value)}><PanelRightOpen size={17} /></button></div></header>
        <div className="reading-layout">{toc && headings.length > 0 && <nav className="contents" aria-label="Note contents"><span className="eyebrow">CONTENTS</span>{headings.map(heading => <button key={heading.id} className={heading.level === 3 ? 'subheading' : ''} onClick={() => reader.current?.querySelector(`#${heading.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>{heading.text}</button>)}</nav>}
          <div className="reading-scroll" ref={reader}>{raw ? <pre className="raw-markdown">{state.markdown}</pre> : <Reader markdown={state.markdown} onHeadings={onHeadings} />}</div>
        </div></> : <div className="empty-workspace"><div className="empty-symbol"><BookOpen size={35} strokeWidth={1.3} /><span /></div>
          <span className="eyebrow">A LITTLE LESS READING. A LOT MORE UNDERSTANDING.</span><h1>{result && !hasNotes ? 'Your exported notes are kept.' : 'Make room for the ideas.'}</h1>
          <p>{result && !hasNotes ? 'The local cache has been cleared. Open your saved file to read your notes; Notion pages stay unchanged.' : 'Bring your slides or PDF. Get clear, connected notes with the formulas, examples and code that matter.'}</p>
          {!state.file && <button className="button primary" disabled={!state.ready} onClick={() => void perform('pick_file')}><Upload size={16} />Choose a document</button>}
          <div className="empty-features"><span><Check size={14} />Markdown & Notebook</span><span><Check size={14} />Resume anytime</span><span><Check size={14} />Your language</span></div></div>}
      {source && <Source state={state} onClose={closeSource} />}
    </section>
    <footer className="activity-bar"><div className="activity-summary"><div className="activity-line"><span className={`activity-indicator ${state.busy ? 'running' : ''}`}>{state.busy ? <LoaderCircle size={16} className="spin" /> : result?.status === 'completed' ? <Check size={16} /> : <span className="status-dot" />}</span>
      <span className={notice ? 'error-text' : ''} role="status">{notice || state.message}</span><button className="icon" aria-label="Activity details" onClick={() => setActivity(value => !value)}><ChevronDown size={15} className={activity ? 'flipped' : ''} /></button></div>
      {result && <div className="progress-caption">{hasNotes ? `${completed} / ${result.page_count} pages saved · ` : ''}{money(result.cost_usd)} spent / reserved{result.retry_at && <span> · Free retry {new Date(result.retry_at * 1000).toLocaleString()}</span>}</div>}
      {result && hasNotes && <div className="progress-track"><span style={{ width: `${Math.min(100, completed / Math.max(1, result.page_count) * 100)}%` }} /></div>}</div>
      <div className="main-actions">{state.busy ? <button className="button" disabled={state.closing} onClick={() => { setNotice('Stopping… Accepted pages are saved.'); void api('cancel'); }}><Square size={13} />{state.closing ? 'Closing…' : 'Cancel'}</button> : <>
        {result?.paid_eligible && state.mode === 'resume' && result.status !== 'completed' && <button className="button" disabled={!config.paid_enabled || busy} onClick={() => setPaidConfirm(true)}>Use paid backup</button>}
        {result && <div className="more-holder"><button className="button more-button" aria-label="More note actions" disabled={busy} onClick={() => setMore(value => !value)}><MoreHorizontal size={19} /></button>
          {more && <div className="action-menu">{hasNotes && result.status === 'completed' && <button onClick={() => { setRevision(true); setMore(false); }}>Edit notes</button>}
            {result.page_count > 0 && <button onClick={() => { setSource(true); setMore(false); }}>Review sources</button>}
            {hasNotes && <button onClick={() => void perform('export')}>Export {result.status === 'completed' ? 'again' : 'available pages'}</button>}
            {!!result.notion && <button onClick={() => void perform('open_notion')}>Open Notion page <ArrowUpRight size={13} /></button>}
            {hasNotes && result.status === 'completed' && <button onClick={() => { setMore(false); if (needsPublishConfirm) setPublishConfirm(true); else void perform('publish'); }}>{result.reason === 'notion_failed' ? 'Retry Notion' : result.notion ? 'Send revision to Notion' : 'Send to Notion'}</button>}
            {result.reason === 'uncertain_cost' && <button onClick={() => { setReconcile(true); setMore(false); }}>Reconcile charge</button>}
          </div>}</div>}
        <button className="button primary create-button" disabled={!state.ready || busy || waiting || (!state.file && state.mode === 'create')} onClick={primary}>{waiting ? <><Clock3 size={16} />Waiting for free quota</> : <>{labels[state.mode]}{state.mode === 'open' ? <ArrowUpRight size={16} /> : null}</>}</button></>}
      </div>
      {activity && <div className="activity-log" aria-label="Activity log"><header><strong>Activity</strong><button className="icon" aria-label="Close activity" onClick={() => setActivity(false)}><X size={15} /></button></header>{state.events.slice().reverse().map((event, index) => <div key={`${event.time}-${index}`}><time>{new Date(event.time * 1000).toLocaleTimeString()}</time><span>{event.message}</span></div>)}</div>}
    </footer>
    {settings && <Settings state={state} onClose={closeSettings} report={setNotice} />}
    {revision && <Revision state={state} onClose={closeRevision} report={setNotice} />}
    {reconcile && <Reconcile state={state} onClose={closeReconcile} report={setNotice} />}
    {publishConfirm && <Modal title="Publish another copy?" onClose={() => setPublishConfirm(false)}><div className="modal-body"><p>Check the destination page first. A previous write may already have reached Notion. Publishing this revision appends content or creates a new page; existing notes are kept.</p></div><footer className="modal-actions"><button className="button" onClick={() => setPublishConfirm(false)}>Cancel</button><button className="button primary" onClick={() => { setPublishConfirm(false); void perform('publish', { confirm_repeat: true }); }}>Publish copy</button></footer></Modal>}
    {paidConfirm && <Modal title="Continue with paid backup?" onClose={() => setPaidConfirm(false)}><div className="modal-body"><p>Use {String(config.paid_model)} for this task. Every request must fit your {money(Number(config.document_limit))} document cap and {money(Number(config.monthly_limit))} monthly cap. A charge with an uncertain outcome pauses the task.</p></div><footer className="modal-actions"><button className="button" onClick={() => setPaidConfirm(false)}>Cancel</button><button className="button primary" onClick={() => { setPaidConfirm(false); void perform('start', { resume: true, allow_paid: true }); }}>Continue within budgets</button></footer></Modal>}
  </main>;
}

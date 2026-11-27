import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Reader } from './Reader';
import { cacheSelection, type CacheRow } from './api';
import { normalizeMath } from './math';

describe('safe study-note reader', () => {
  it('renders math, code whitespace and legacy HTML tables', () => {
    const code = 'answer = (\n    42\n)\nmessage = """\n123\n\n\nkept  \n"""\n';
    const { container } = render(<Reader markdown={'## Topic\n\n$$w_{t+1}=w_t-\\eta\\nabla f(w_t)$$\n\n```python\n' + code + '```\n\n<table><tr><th>Method</th></tr><tr><td>VAE</td></tr></table>'} />);
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.querySelector('pre code')?.textContent).toBe(code);
    expect(screen.getByRole('table')).toHaveTextContent('VAE');
  });
  it('removes scripts, remote images, event handlers and unsafe links', () => {
    const { container } = render(<Reader markdown={'<script>window.stolen = 1</script>\n\n<img src="https://example.com/pixel" onerror="alert(1)">\n\n[unsafe](file:///etc/passwd)\n\n<a href="javascript:alert(1)">bad</a>\n\n<iframe src="https://example.com"></iframe>'} />);
    expect(container.querySelector('script,img,iframe,[onerror],[href^="javascript:"],[href^="file:"]')).toBeNull();
  });
  it('renders legacy math delimiters while preserving fenced and inline code byte for byte', () => {
    const code='```python\nvalue = r"\\(literal\\)"\n\n\n```\n';
    expect(normalizeMath(code)).toBe(code);
    expect(normalizeMath('Use `\\(literal\\)` and \\(x=2\\).')).toBe('Use `\\(literal\\)` and $x=2$.');
    const {container}=render(<Reader markdown={'\\[x=2\\]\n\n'+code} />);
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.querySelector('pre code')?.textContent).toBe('value = r"\\(literal\\)"\n\n\n');
  });
  it('keeps later sections readable after a real-model closing math delimiter on the formula line', () => {
    const { container } = render(<Reader markdown={'## Rule\n\n$$\n\\begin{cases}0 & x<0\\\\1 & x>0\\end{cases}$$\n\n## Later topic\n\nExplanation.\n\n```python\nvalue = 42\n```'} />);
    expect(screen.getByRole('heading', { name:'Later topic' })).toBeVisible();
    expect(container.querySelector('pre code')?.textContent).toBe('value = 42\n');
    expect(container.querySelector('.katex-error')).toBeNull();
  });
});
describe('shared-cache estimate', () => {
  const row = { id: 'one', cache_key: 'shared', document: 'Course.pdf', body_bytes: 100, shared_bytes: 1000, shared_tasks: 2, protected_reason: '' } as CacheRow;
  it('counts shared data once and keeps it for unselected tasks', () => {
    const rows = [row, { ...row, id: 'two', body_bytes: 200 }];
    expect(cacheSelection(rows, new Set(['one']))).toEqual({ size: 100, sharedKept: true });
    expect(cacheSelection(rows, new Set(['one', 'two']))).toEqual({ size: 1300, sharedKept: false });
  });
});

import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { expect, test, vi } from 'vitest';

test('native bootstrap exposes only two commands without dynamic code evaluation', async () => {
  const callbacks: Record<string, Record<string, (value: unknown) => void>> = {};
  const received = vi.fn((name: string, args: unknown[], id: string) => callbacks[name][id](args));
  const bridge = { api: {} as Record<string, (...args: unknown[]) => Promise<unknown>>,
    _returnValuesCallbacks: callbacks, _jsApiCallback: received,
    _checkValue: (name: string, resolve: (value: unknown) => void, _reject: unknown, id: string) => { callbacks[name][id] = resolve; },
    _createApi: (_functions: unknown[]) => {},
  };
  const blocked = () => { throw new Error('CSP blocks dynamic code'); };
  runInNewContext(readFileSync('../app/desktop/bridge.js', 'utf8'),
    { window: { pywebview: bridge }, Function: blocked, eval: blocked });
  bridge._createApi([{ func: 'get_state' }, { func: 'call' }, { func: 'service.credentials' }]);
  expect(Object.keys(bridge.api).sort()).toEqual(['call', 'get_state']);
  const result = await bridge.api.call('cancel', {});
  expect(Array.from(result as unknown[])).toEqual(['cancel', {}]);
  expect(received).toHaveBeenCalledOnce();
});

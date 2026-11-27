// pywebview 6.2 CSP-compatible API creation: closures, never eval/Function.
// The Python host exposes exactly these two methods, not the service object.
window.pywebview._createApi = function (functions) {
  for (const entry of functions) {
    const name = entry.func;
    if (name !== 'get_state' && name !== 'call') continue;
    window.pywebview._returnValuesCallbacks[name] = {};
    window.pywebview.api[name] = function (...args) {
      const id = String(Math.random()).substring(2);
      const promise = new Promise((resolve, reject) => {
        window.pywebview._checkValue(name, resolve, reject, id);
      });
      window.pywebview._jsApiCallback(name, args, id);
      return promise;
    };
  }
};

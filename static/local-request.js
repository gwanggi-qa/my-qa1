/* Per-process anti-forgery bootstrap. No storage or persistent credentials. */
(() => {
  'use strict';
  const token = document.querySelector('meta[name="qa-csrf-token"]')?.content;
  if (!token) throw new Error('페이지 검증값이 없습니다. 화면을 새로고침해 주세요.');
  const originalFetch = window.fetch.bind(window);
  const writes = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  window.fetch = (input, init = {}) => {
    const isRequest = input instanceof Request;
    const url = new URL(isRequest ? input.url : String(input), location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) return originalFetch(input, init);
    const options = {...init};
    const method = (options.method || (isRequest ? input.method : 'GET')).toUpperCase();
    const headers = new Headers(options.headers || (isRequest ? input.headers : undefined));
    headers.set('X-QA-CSRF-Token', token);
    if (writes.has(method) && options.body === undefined && (!isRequest || input.body === null)) {
      options.body = '{}';
      headers.set('Content-Type', 'application/json');
    }
    options.headers = headers;
    options.credentials = 'same-origin';
    return originalFetch(input, options);
  };
  Object.defineProperty(window, 'QALocalAccess', {value: Object.freeze({
    socketOptions: () => ({transports: ['websocket'], auth: {csrf_token: token}}),
    socketPayload: () => ({csrf_token: token})
  })});
})();

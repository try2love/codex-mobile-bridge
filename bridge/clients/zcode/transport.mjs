import { pathToFileURL } from 'node:url';
import { nativeRequest } from './renderer.mjs';

export function verifiedTarget(targets, application, port) {
  const expected = pathToFileURL(application + '/Contents/Resources/app.asar/out/renderer/index.html').href;
  const matches = targets.filter(t => t.type === 'page' && t.url.split('?')[0].split('#')[0] === expected);
  if (matches.length !== 1) throw Error('ZCode native window is missing or ambiguous');
  const endpoint = new URL(matches[0].webSocketDebuggerUrl);
  if (endpoint.protocol !== 'ws:' || endpoint.hostname !== '127.0.0.1' ||
      endpoint.port !== String(port) || endpoint.username || endpoint.password ||
      !/^\/devtools\/page\/[A-Za-z0-9_-]+$/.test(endpoint.pathname) || endpoint.search || endpoint.hash)
    throw Error('Untrusted ZCode debugging endpoint');
  return endpoint.href;
}

export async function requestNative(request) {
  const port = request.port;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw Error('Invalid loopback port');
  const remaining = () => Math.max(1, request.expiresAt - Date.now());
  const response = await fetch(`http://127.0.0.1:${port}/json/list`, {
    redirect: 'error', signal: AbortSignal.timeout(Math.min(3000, remaining())) });
  if (!response.ok) throw Error('Native ZCode target discovery failed');
  const ws = new WebSocket(verifiedTarget(await response.json(), request.application, port));
  let timer;
  try {
    return await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(Error('Native request timeout; do not replay')), remaining());
      ws.onerror = () => reject(Error('Native connection failed; do not replay'));
      ws.onclose = () => reject(Error('Native connection closed; do not replay'));
      ws.onopen = () => ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: {
        expression: `(${nativeRequest.toString()})(${JSON.stringify(request)})`,
        awaitPromise: true, returnByValue: true } }));
      ws.onmessage = event => {
        const reply = JSON.parse(event.data);
        if (reply.id !== 1) return;
        const error = reply.error?.message || reply.result?.exceptionDetails?.exception?.description || reply.result?.exceptionDetails?.text;
        if (error) reject(Error(error));
        else resolve(reply.result.result.value);
      };
    });
  } finally { clearTimeout(timer); ws.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    let input = '';
    for await (const part of process.stdin) input += part;
    console.log(JSON.stringify(await requestNative(JSON.parse(input))));
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

#!/usr/bin/env node
/**
 * pythinker WhatsApp Bridge
 * 
 * This bridge connects WhatsApp Web to pythinker's Python backend
 * via WebSocket. It handles authentication, message forwarding,
 * and reconnection logic.
 * 
 * Usage:
 *   npm run build && npm start
 *   
 * Or with custom settings:
 *   BRIDGE_PORT=3001 AUTH_DIR=~/.pythinker/whatsapp npm start
 *
 * To avoid the large terminal QR code, provide a digits-only phone number:
 *   WHATSAPP_PAIRING_PHONE=15551234567 npm start
 */

// Polyfill crypto for Baileys in ESM
import { webcrypto } from 'crypto';
if (!globalThis.crypto) {
  (globalThis as any).crypto = webcrypto;
}

import { homedir } from 'os';
import { join } from 'path';

const SENSITIVE_CONSOLE_KEYS = new Set([
  'auth',
  'baseKey',
  'chainKey',
  'creds',
  'currentRatchet',
  'ephemeralKeyPair',
  'lastRemoteEphemeralKey',
  'mediaKey',
  'messageKeys',
  'pendingPreKey',
  'privKey',
  'remoteIdentityKey',
  'rootKey',
  'sessions',
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Object.prototype.toString.call(value) === '[object Object]';
}

function isSignalSessionLike(value: unknown): boolean {
  if (!isPlainObject(value)) return false;
  return Boolean(value.registrationId && value.currentRatchet && value.indexInfo);
}

function redactConsoleValue(value: unknown, seen = new WeakSet<object>(), depth = 0): unknown {
  if (typeof Buffer !== 'undefined' && Buffer.isBuffer(value)) {
    return `<Buffer redacted length=${value.length}>`;
  }
  if (value instanceof Uint8Array) {
    return `<Uint8Array redacted length=${value.byteLength}>`;
  }
  if (value instanceof Error) {
    const error = value as Error & { code?: unknown; status?: unknown; output?: { statusCode?: unknown } };
    return {
      name: error.name,
      message: error.message,
      code: error.code,
      status: error.status || error.output?.statusCode,
    };
  }
  if (!value || typeof value !== 'object') return value;
  if (seen.has(value)) return '[Circular]';
  if (isSignalSessionLike(value)) return '[redacted Signal session]';
  if (depth >= 4) return '[Object]';

  seen.add(value);
  if (Array.isArray(value)) {
    return value.map((item) => redactConsoleValue(item, seen, depth + 1));
  }

  const redacted: Record<string, unknown> = {};
  for (const [key, nested] of Object.entries(value as Record<string, unknown>)) {
    redacted[key] = SENSITIVE_CONSOLE_KEYS.has(key)
      ? '[redacted]'
      : redactConsoleValue(nested, seen, depth + 1);
  }
  return redacted;
}

function installConsoleRedaction(): void {
  for (const method of ['debug', 'error', 'info', 'log', 'warn'] as const) {
    const original = console[method].bind(console);
    console[method] = (...args: unknown[]) => original(...args.map((arg) => redactConsoleValue(arg)));
  }
}

installConsoleRedaction();

const PORT = parseInt(process.env.BRIDGE_PORT || '3001', 10);
const AUTH_DIR = process.env.AUTH_DIR || join(homedir(), '.pythinker', 'whatsapp-auth');
const TOKEN = process.env.BRIDGE_TOKEN?.trim();
const PAIRING_PHONE = process.env.WHATSAPP_PAIRING_PHONE?.replace(/\D/g, '') || undefined;

const parsePositiveInt = (raw: string | undefined): number | undefined => {
  if (!raw) return undefined;
  const n = parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : undefined;
};

const TUNING = {
  keepAliveIntervalMs: parsePositiveInt(process.env.BRIDGE_KEEPALIVE_MS),
  connectTimeoutMs: parsePositiveInt(process.env.BRIDGE_CONNECT_TIMEOUT_MS),
  defaultQueryTimeoutMs: parsePositiveInt(process.env.BRIDGE_QUERY_TIMEOUT_MS),
};

if (!TOKEN) {
  console.error('BRIDGE_TOKEN is required. Start the bridge via pythinker so it can provision a local secret automatically.');
  process.exit(1);
}

console.log('🤖 pythinker WhatsApp Bridge');
console.log('========================\n');

// Load Baileys after installing console redaction so upstream libsignal
// diagnostics cannot print Signal session keys to stdout/stderr.
const { BridgeServer } = await import('./server.js');
const server = new BridgeServer(PORT, AUTH_DIR, TOKEN, PAIRING_PHONE, TUNING);

// Handle graceful shutdown
process.on('SIGINT', async () => {
  console.log('\n\nShutting down...');
  await server.stop();
  process.exit(0);
});

process.on('SIGTERM', async () => {
  await server.stop();
  process.exit(0);
});

// Start the server
server.start().catch((error) => {
  console.error('Failed to start bridge:', error);
  process.exit(1);
});

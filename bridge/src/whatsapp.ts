/**
 * WhatsApp client wrapper using Baileys.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadMediaMessage,
  extractMessageContent as baileysExtractMessageContent,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import { readFile, writeFile, mkdir } from 'fs/promises';
import { join, basename } from 'path';
import { randomBytes } from 'crypto';

const VERSION = '0.1.0';
const MEDIA_DOWNLOAD_ATTEMPTS = 3;
const MEDIA_RETRY_BASE_MS = 500;
const MEDIA_RETRY_MAX_MS = 2500;

type PresenceState = 'available' | 'unavailable' | 'composing' | 'recording' | 'paused';

export interface InboundMessage {
  id: string;
  sender: string;
  pn: string;
  content: string;
  timestamp: number;
  isGroup: boolean;
  wasMentioned?: boolean;
  media?: string[];
}

export interface WhatsAppClientOptions {
  authDir: string;
  pairingPhone?: string;
  keepAliveIntervalMs?: number;
  connectTimeoutMs?: number;
  defaultQueryTimeoutMs?: number;
  onMessage: (msg: InboundMessage) => void;
  onQR: (qr: string) => void;
  onStatus: (status: string) => void;
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private mediaLogger = pino({ level: 'silent' });
  private reconnecting = false;
  private pairingCodeRequested = false;
  private pairingNoticePrinted = false;

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  private normalizeJid(jid: string | undefined | null): string {
    return (jid || '').split(':')[0];
  }

  private wasMentioned(msg: any): boolean {
    if (!msg?.key?.remoteJid?.endsWith('@g.us')) return false;

    const candidates = [
      msg?.message?.extendedTextMessage?.contextInfo?.mentionedJid,
      msg?.message?.imageMessage?.contextInfo?.mentionedJid,
      msg?.message?.videoMessage?.contextInfo?.mentionedJid,
      msg?.message?.documentMessage?.contextInfo?.mentionedJid,
      msg?.message?.audioMessage?.contextInfo?.mentionedJid,
    ];
    const mentioned = candidates.flatMap((items) => (Array.isArray(items) ? items : []));
    if (mentioned.length === 0) return false;

    const selfIds = new Set(
      [this.sock?.user?.id, this.sock?.user?.lid, this.sock?.user?.jid]
        .map((jid) => this.normalizeJid(jid))
        .filter(Boolean),
    );
    return mentioned.some((jid: string) => selfIds.has(this.normalizeJid(jid)));
  }

  private async requestPairingCode(): Promise<void> {
    if (!this.options.pairingPhone || this.pairingCodeRequested) return;
    if (this.sock?.authState?.creds?.registered) return;

    this.pairingCodeRequested = true;
    try {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const code = await this.sock.requestPairingCode(this.options.pairingPhone);
      console.log('\n🔢 WhatsApp pairing code: ' + code);
      console.log('Open WhatsApp → Linked Devices → Link with phone number instead.\n');
    } catch (err) {
      this.pairingCodeRequested = false;
      console.error('Failed to request WhatsApp pairing code:', err);
    }
  }

  async connect(): Promise<void> {
    const logger = pino({ level: 'silent' });
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`Using Baileys version: ${version.join('.')}`);
    if (!state.creds.registered) {
      this.pairingCodeRequested = false;
      this.pairingNoticePrinted = false;
    }

    // Create socket
    const socketOpts: any = {
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser: ['pythinker', 'cli', VERSION],
      syncFullHistory: false,
      markOnlineOnConnect: false,
    };
    if (this.options.keepAliveIntervalMs) socketOpts.keepAliveIntervalMs = this.options.keepAliveIntervalMs;
    if (this.options.connectTimeoutMs) socketOpts.connectTimeoutMs = this.options.connectTimeoutMs;
    if (this.options.defaultQueryTimeoutMs) socketOpts.defaultQueryTimeoutMs = this.options.defaultQueryTimeoutMs;
    this.sock = makeWASocket(socketOpts);

    // Handle WebSocket errors
    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    if (this.options.pairingPhone && !state.creds.registered) {
      console.log('\n📱 Pairing-code mode enabled; terminal QR display suppressed.\n');
      this.pairingNoticePrinted = true;
      void this.requestPairingCode();
    }

    // Handle connection updates
    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        if (this.options.pairingPhone) {
          if (!this.pairingNoticePrinted) {
            console.log('\n📱 Pairing-code mode enabled; terminal QR display suppressed.\n');
            this.pairingNoticePrinted = true;
          }
          await this.requestPairingCode();
        } else {
          // Display QR code in terminal
          console.log('\n📱 Scan this QR code with WhatsApp (Linked Devices):\n');
          qrcode.generate(qr, { small: true });
          this.options.onQR(qr);
        }
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(`Connection closed. Status: ${statusCode}, Will reconnect: ${shouldReconnect}`);
        this.options.onStatus('disconnected');

        if (shouldReconnect && !this.reconnecting) {
          this.reconnecting = true;
          console.log('Reconnecting in 5 seconds...');
          setTimeout(() => {
            this.reconnecting = false;
            this.connect();
          }, 5000);
        }
      } else if (connection === 'open') {
        console.log('✅ Connected to WhatsApp');
        this.options.onStatus('connected');
      }
    });

    // Save credentials on update
    this.sock.ev.on('creds.update', saveCreds);

    // Handle incoming messages
    this.sock.ev.on('messages.upsert', async ({ messages, type }: { messages: any[]; type: string }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        if (msg.key.fromMe) continue;
        if (msg.key.remoteJid === 'status@broadcast') continue;

        const unwrapped = baileysExtractMessageContent(msg.message);
        if (!unwrapped) continue;

        const content = this.getTextContent(unwrapped);
        let fallbackContent: string | null = null;
        const mediaPaths: string[] = [];

        if (unwrapped.imageMessage) {
          fallbackContent = '[Image]';
          const path = await this.downloadMedia(
            msg,
            'imageMessage',
            unwrapped.imageMessage,
            unwrapped.imageMessage.mimetype ?? undefined,
          );
          if (path) mediaPaths.push(path);
        } else if (unwrapped.documentMessage) {
          fallbackContent = '[Document]';
          const path = await this.downloadMedia(
            msg,
            'documentMessage',
            unwrapped.documentMessage,
            unwrapped.documentMessage.mimetype ?? undefined,
            unwrapped.documentMessage.fileName ?? undefined,
          );
          if (path) mediaPaths.push(path);
        } else if (unwrapped.videoMessage) {
          fallbackContent = '[Video]';
          const path = await this.downloadMedia(
            msg,
            'videoMessage',
            unwrapped.videoMessage,
            unwrapped.videoMessage.mimetype ?? undefined,
          );
          if (path) mediaPaths.push(path);
        } else if (unwrapped.audioMessage) {
          fallbackContent = '[Voice Message]';
          const path = await this.downloadMedia(
            msg,
            'audioMessage',
            unwrapped.audioMessage,
            unwrapped.audioMessage.mimetype ?? undefined,
          );
          if (path) mediaPaths.push(path);
        }

        const finalContent = content || (mediaPaths.length === 0 ? fallbackContent : '') || '';
        if (!finalContent && mediaPaths.length === 0) continue;

        const isGroup = msg.key.remoteJid?.endsWith('@g.us') || false;
        const wasMentioned = this.wasMentioned(msg);

        this.options.onMessage({
          id: msg.key.id || '',
          sender: msg.key.remoteJid || '',
          pn: msg.key.remoteJidAlt || '',
          content: finalContent,
          timestamp: msg.messageTimestamp as number,
          isGroup,
          ...(isGroup ? { wasMentioned } : {}),
          ...(mediaPaths.length > 0 ? { media: mediaPaths } : {}),
        });
      }
    });
  }

  private async downloadMedia(
    msg: any,
    mediaType: string,
    mediaNode: any,
    mimetype?: string,
    fileName?: string,
  ): Promise<string | null> {
    const metadata = this.describeMedia(msg, mediaType, mediaNode);
    const validationError = this.validateMediaForDownload(mediaNode);
    if (validationError) {
      console.warn('Skipping WhatsApp media download:', { ...metadata, reason: validationError });
      return null;
    }

    try {
      const mediaDir = join(this.options.authDir, '..', 'media');
      await mkdir(mediaDir, { recursive: true });

      const buffer = await this.downloadMediaBufferWithRetry(msg, metadata);

      let outFilename: string;
      if (fileName) {
        // Documents have a filename — use it with a unique prefix to avoid collisions
        const prefix = `wa_${Date.now()}_${randomBytes(4).toString('hex')}_`;
        outFilename = prefix + basename(fileName);
      } else {
        const mime = mimetype || 'application/octet-stream';
        // Derive extension from mimetype subtype (e.g. "image/png" → ".png", "application/pdf" → ".pdf")
        const ext = '.' + (mime.split('/').pop()?.split(';')[0] || 'bin');
        outFilename = `wa_${Date.now()}_${randomBytes(4).toString('hex')}${ext}`;
      }

      const filepath = join(mediaDir, outFilename);
      await writeFile(filepath, buffer);

      return filepath;
    } catch (err) {
      console.warn('WhatsApp media download failed:', {
        ...metadata,
        error: this.describeMediaDownloadError(err),
      });
      return null;
    }
  }

  private async downloadMediaBufferWithRetry(
    msg: any,
    metadata: Record<string, unknown>,
  ): Promise<Buffer> {
    let lastError: unknown;
    for (let attempt = 1; attempt <= MEDIA_DOWNLOAD_ATTEMPTS; attempt += 1) {
      try {
        const ctx = this.sock?.updateMediaMessage
          ? {
              logger: this.mediaLogger,
              reuploadRequest: this.sock.updateMediaMessage.bind(this.sock),
            }
          : undefined;
        return await downloadMediaMessage(msg, 'buffer', {}, ctx) as Buffer;
      } catch (err) {
        lastError = err;
        if (attempt >= MEDIA_DOWNLOAD_ATTEMPTS || !this.shouldRetryMediaDownload(err)) {
          throw err;
        }
        const delayMs = this.mediaRetryDelayMs(attempt);
        console.warn('WhatsApp media download attempt failed; retrying:', {
          ...metadata,
          attempt,
          nextAttempt: attempt + 1,
          delayMs,
          error: this.describeMediaDownloadError(err),
        });
        await this.sleep(delayMs);
      }
    }
    throw lastError;
  }

  private validateMediaForDownload(mediaNode: any): string | null {
    if (!this.hasUsableMediaKey(mediaNode?.mediaKey)) {
      return 'missing mediaKey; media cannot be decrypted';
    }

    const hasDirectPath = typeof mediaNode?.directPath === 'string' && mediaNode.directPath.length > 0;
    const hasThumbnailDirectPath = typeof mediaNode?.thumbnailDirectPath === 'string'
      && mediaNode.thumbnailDirectPath.length > 0;
    const hasMmgUrl = typeof mediaNode?.url === 'string'
      && mediaNode.url.startsWith('https://mmg.whatsapp.net/');
    if (!hasDirectPath && !hasThumbnailDirectPath && !hasMmgUrl) {
      return 'missing valid media URL/directPath';
    }

    return null;
  }

  private hasUsableMediaKey(mediaKey: unknown): boolean {
    if (typeof mediaKey === 'string') return mediaKey.trim().length > 0;
    if (Buffer.isBuffer(mediaKey)) return mediaKey.byteLength > 0;
    if (mediaKey instanceof Uint8Array) return mediaKey.byteLength > 0;
    if (Array.isArray(mediaKey)) return mediaKey.length > 0;
    return Boolean(mediaKey);
  }

  private shouldRetryMediaDownload(err: unknown): boolean {
    const status = this.mediaErrorStatus(err);
    if (status && [400, 401, 403].includes(status)) return false;
    if (status && [408, 410, 425, 429, 500, 502, 503, 504].includes(status)) return true;

    const message = this.mediaErrorMessage(err).toLowerCase();
    if (
      message.includes('empty media key')
      || message.includes('no valid media url')
      || message.includes('not a media message')
    ) {
      return false;
    }

    return [
      'fetch failed',
      'network',
      'timeout',
      'timed out',
      'econnreset',
      'etimedout',
      'und_err',
      'aborted',
      'socket hang up',
    ].some((needle) => message.includes(needle));
  }

  private mediaRetryDelayMs(attempt: number): number {
    const base = Math.min(MEDIA_RETRY_BASE_MS * (2 ** (attempt - 1)), MEDIA_RETRY_MAX_MS);
    return base + Math.floor(Math.random() * 250);
  }

  private describeMedia(msg: any, mediaType: string, mediaNode: any): Record<string, unknown> {
    const remoteJid = msg?.key?.remoteJid || '';
    return {
      messageId: msg?.key?.id || '',
      chatKind: remoteJid.endsWith('@newsletter')
        ? 'newsletter'
        : remoteJid.endsWith('@g.us') ? 'group' : 'direct',
      mediaType,
      mimetype: mediaNode?.mimetype,
      hasMediaKey: this.hasUsableMediaKey(mediaNode?.mediaKey),
      hasDirectPath: typeof mediaNode?.directPath === 'string' && mediaNode.directPath.length > 0,
      hasThumbnailDirectPath: typeof mediaNode?.thumbnailDirectPath === 'string'
        && mediaNode.thumbnailDirectPath.length > 0,
      hasUrl: typeof mediaNode?.url === 'string' && mediaNode.url.length > 0,
      hasMmgUrl: typeof mediaNode?.url === 'string'
        && mediaNode.url.startsWith('https://mmg.whatsapp.net/'),
      fileLength: mediaNode?.fileLength?.toString?.(),
    };
  }

  private describeMediaDownloadError(err: unknown): Record<string, unknown> {
    const error = err as any;
    return {
      name: error?.name || error?.constructor?.name || 'Error',
      message: this.mediaErrorMessage(err),
      status: this.mediaErrorStatus(err),
      code: error?.code || error?.cause?.code,
      isBoom: Boolean(error?.isBoom),
    };
  }

  private mediaErrorMessage(err: unknown): string {
    if (err instanceof Error) return err.message;
    return String(err);
  }

  private mediaErrorStatus(err: unknown): number | undefined {
    const error = err as any;
    return error?.status || error?.output?.statusCode || error?.output?.payload?.statusCode;
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private getTextContent(message: any): string | null {
    // Text message
    if (message.conversation) {
      return message.conversation;
    }

    // Extended text (reply, link preview)
    if (message.extendedTextMessage?.text) {
      return message.extendedTextMessage.text;
    }

    // Image with optional caption
    if (message.imageMessage) {
      return message.imageMessage.caption || '';
    }

    // Video with optional caption
    if (message.videoMessage) {
      return message.videoMessage.caption || '';
    }

    // Document with optional caption
    if (message.documentMessage) {
      return message.documentMessage.caption || '';
    }

    // Voice/Audio message
    if (message.audioMessage) {
      return `[Voice Message]`;
    }

    return null;
  }

  async sendMessage(to: string, text: string): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    await this.sock.sendMessage(to, { text });
  }

  async setPresence(to: string | undefined, state: PresenceState): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    const jid = state === 'available' || state === 'unavailable' ? undefined : to;
    if (!jid && state !== 'available' && state !== 'unavailable') {
      throw new Error('Presence target required');
    }

    await this.sock.sendPresenceUpdate(state, jid);
  }

  async sendReadReceipt(keys: Array<{ remoteJid: string; id: string; fromMe?: boolean }>): Promise<void> {
    if (!this.sock || keys.length === 0) {
      return;
    }
    try {
      await this.sock.readMessages(keys.map((k) => ({ ...k, fromMe: k.fromMe ?? false })));
    } catch (err) {
      console.debug('readMessages failed:', err);
    }
  }

  async sendMedia(
    to: string,
    filePath: string,
    mimetype: string,
    caption?: string,
    fileName?: string,
  ): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    const buffer = await readFile(filePath);
    const category = mimetype.split('/')[0];

    if (category === 'image') {
      await this.sock.sendMessage(to, { image: buffer, caption: caption || undefined, mimetype });
    } else if (category === 'video') {
      await this.sock.sendMessage(to, { video: buffer, caption: caption || undefined, mimetype });
    } else if (category === 'audio') {
      // Treat opus/ogg as a proper voice note (ptt) so WhatsApp renders the
      // waveform + 1.5x/2x playback controls.
      const ptt = /^audio\/(ogg|opus)/i.test(mimetype);
      await this.sock.sendMessage(to, { audio: buffer, mimetype, ptt });
    } else {
      const name = fileName || basename(filePath);
      await this.sock.sendMessage(to, { document: buffer, mimetype, fileName: name });
    }
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
  }
}

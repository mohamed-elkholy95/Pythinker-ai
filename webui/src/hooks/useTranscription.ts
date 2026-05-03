import { useCallback, useEffect, useRef, useState } from "react";

import type { PythinkerClient } from "@/lib/pythinker-client";
import type { InboundEvent } from "@/lib/types";

/** Class for errors surfaced through the hook's ``error`` state. The two
 * categories are kept separate so the composer can pick a different toast
 * (e.g. permission errors deserve "Open browser settings" guidance). */
export type TranscriptionErrorKind = "permission" | "generic";

export interface UseTranscriptionApi {
  /** True between ``start()`` resolving and ``stop()`` being called. */
  recording: boolean;
  /** True between ``stop()`` being called and the server reply arriving
   * (or the 30s timeout firing). */
  transcribing: boolean;
  /** Last error category, or null after a successful turn. The composer
   * decides whether to surface it as a toast / inline notice. */
  error: TranscriptionErrorKind | null;
  /** Open the mic and start recording. Resolves once the MediaRecorder has
   * actually entered the recording state. Rejects on permission denial. */
  start: () => Promise<void>;
  /** Stop recording and submit the captured blob for transcription.
   * Resolves with the transcribed text on success, or ``null`` on error
   * (the ``error`` state is updated alongside). */
  stop: () => Promise<string | null>;
}

/** ``MediaRecorder.mimeType`` returns strings like ``"audio/webm;codecs=opus"``;
 * we only need the container hint for the backend's ``format`` field. */
function detectFormat(mimeType: string): string {
  const lower = (mimeType ?? "").toLowerCase();
  if (lower.includes("mp4")) return "mp4";
  if (lower.includes("wav")) return "wav";
  // Default both for empty strings (older Safari) and explicit webm.
  return "webm";
}

/** Strip the ``data:<mime>;base64,`` prefix from a ``FileReader.readAsDataURL``
 * result so we ship pure base64 to the backend (which prepends its own
 * MIME via the ``format`` field). */
function stripDataUrlPrefix(dataUrl: string): string {
  const comma = dataUrl.indexOf(",");
  return comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
}

function readBlobAsBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error("FileReader returned non-string result"));
        return;
      }
      resolve(stripDataUrlPrefix(result));
    };
    reader.onerror = () => reject(reader.error ?? new Error("FileReader error"));
    reader.readAsDataURL(blob);
  });
}

const TRANSCRIBE_TIMEOUT_MS = 30_000;

/**
 * MediaRecorder lifecycle wrapper that talks to ``client.transcribe`` and
 * resolves with the matching ``transcription_result`` text.
 *
 * Resource hygiene:
 * - ``stop()`` always halts the underlying ``MediaStream`` tracks (otherwise
 *   the OS mic indicator stays lit).
 * - The 30s timeout and the result subscription clean each other up so we
 *   never leak a pending promise.
 * - On unmount any in-flight stream/recorder/timer is torn down.
 */
export function useTranscription(client: PythinkerClient | null): UseTranscriptionApi {
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<TranscriptionErrorKind | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeTypeRef = useRef<string>("");

  const stopTracks = useCallback(() => {
    const stream = streamRef.current;
    if (stream) {
      try {
        for (const track of stream.getTracks()) track.stop();
      } catch {
        // best-effort cleanup
      }
    }
    streamRef.current = null;
  }, []);

  // Tear everything down on unmount so the mic doesn't stay hot when the
  // composer is unmounted mid-recording (e.g. the user navigates chats).
  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // best-effort
        }
      }
      stopTracks();
    };
  }, [stopTracks]);

  const start = useCallback(async () => {
    if (recording || transcribing) return;
    setError(null);
    if (!navigator?.mediaDevices?.getUserMedia) {
      setError("generic");
      throw new Error("getUserMedia is not available in this environment");
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      // Browsers throw ``NotAllowedError`` when the user denies the prompt;
      // ``NotFoundError`` when no input device exists. Treat the explicit
      // permission denial as the "permission" category, everything else as
      // generic so the composer can localize the message accordingly.
      const name = (err as { name?: string } | null)?.name;
      setError(name === "NotAllowedError" ? "permission" : "generic");
      throw err;
    }
    streamRef.current = stream;
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch (err) {
      stopTracks();
      setError("generic");
      throw err;
    }
    chunksRef.current = [];
    mimeTypeRef.current = recorder.mimeType ?? "";
    recorder.ondataavailable = (ev: BlobEvent) => {
      if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
    };
    recorderRef.current = recorder;
    recorder.start();
    setRecording(true);
  }, [recording, transcribing, stopTracks]);

  const stop = useCallback(async (): Promise<string | null> => {
    const recorder = recorderRef.current;
    if (!recorder || !recording) return null;
    setRecording(false);
    setTranscribing(true);

    // Wait for the recorder to flush its final chunk via ``onstop``.
    const finalBlob = await new Promise<Blob | null>((resolve) => {
      recorder.onstop = () => {
        const chunks = chunksRef.current;
        chunksRef.current = [];
        if (chunks.length === 0) {
          resolve(null);
          return;
        }
        const type = mimeTypeRef.current || chunks[0].type || "audio/webm";
        resolve(new Blob(chunks, { type }));
      };
      try {
        recorder.stop();
      } catch {
        resolve(null);
      }
    });
    recorderRef.current = null;
    stopTracks();

    if (!finalBlob || finalBlob.size === 0 || !client) {
      setTranscribing(false);
      setError("generic");
      return null;
    }

    let base64: string;
    try {
      base64 = await readBlobAsBase64(finalBlob);
    } catch {
      setTranscribing(false);
      setError("generic");
      return null;
    }

    const requestId = crypto.randomUUID();
    const format = detectFormat(mimeTypeRef.current);

    return new Promise<string | null>((resolve) => {
      // Symmetric cleanup for both branches: the timeout clears the
      // subscription, and the subscription clears the timeout — whichever
      // wins disables the other so we can never leak a stray rejection.
      let settled = false;
      const unsubscribe = client.onTranscription((ev: InboundEvent) => {
        if (settled) return;
        if (
          ev.event === "transcription_result" &&
          ev.request_id === requestId
        ) {
          settled = true;
          clearTimeout(timer);
          unsubscribe();
          setTranscribing(false);
          resolve(ev.text);
          return;
        }
        if (
          ev.event === "error" &&
          (ev.request_id === undefined || ev.request_id === requestId)
        ) {
          settled = true;
          clearTimeout(timer);
          unsubscribe();
          setTranscribing(false);
          setError("generic");
          resolve(null);
        }
      });
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        unsubscribe();
        setTranscribing(false);
        setError("generic");
        resolve(null);
      }, TRANSCRIBE_TIMEOUT_MS);

      client.transcribe(base64, format, requestId);
    });
  }, [client, recording, stopTracks]);

  return { recording, transcribing, error, start, stop };
}

import { act, renderHook } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

import { useTranscription } from "@/hooks/useTranscription";
import type { PythinkerClient } from "@/lib/pythinker-client";
import type { InboundEvent } from "@/lib/types";

/**
 * Minimal MediaRecorder mock — happy-dom doesn't ship one. We expose hooks
 * to push chunks via ``ondataavailable`` and trigger the ``onstop`` callback
 * synchronously, mirroring how a real recorder behaves once you call
 * ``stop()``.
 */
class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state: "inactive" | "recording" = "inactive";
  mimeType: string;
  ondataavailable: ((ev: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  // Captured streams so the test can observe ``stream.getTracks().stop()``
  // happened (proxied via the FakeMediaStream below).
  stream: FakeMediaStream;

  constructor(stream: MediaStream, options?: { mimeType?: string }) {
    this.stream = stream as unknown as FakeMediaStream;
    this.mimeType = options?.mimeType ?? "audio/webm;codecs=opus";
    FakeMediaRecorder.instances.push(this);
  }

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    // Real recorders emit one final ``dataavailable`` then ``onstop``. We let
    // tests preload chunks via ``pushChunk`` before calling ``stop`` on the
    // hook, then invoke onstop here so the hook's promise resolves.
    queueMicrotask(() => this.onstop?.());
  }

  pushChunk(data: Blob) {
    this.ondataavailable?.({ data });
  }
}

class FakeMediaStreamTrack {
  stopped = false;
  stop() {
    this.stopped = true;
  }
}

class FakeMediaStream {
  tracks: FakeMediaStreamTrack[];
  constructor() {
    this.tracks = [new FakeMediaStreamTrack()];
  }
  getTracks() {
    return this.tracks;
  }
}

/**
 * Stub PythinkerClient — we only need ``transcribe`` (spied) and
 * ``onTranscription`` (drives the resolution path).
 */
function makeClient(): {
  client: PythinkerClient;
  transcribeSpy: MockInstance;
  emit: (ev: InboundEvent) => void;
} {
  const subscribers = new Set<(ev: InboundEvent) => void>();
  const transcribeSpy = vi.fn();
  const client = {
    transcribe: transcribeSpy,
    onTranscription: (h: (ev: InboundEvent) => void) => {
      subscribers.add(h);
      return () => subscribers.delete(h);
    },
  } as unknown as PythinkerClient;
  const emit = (ev: InboundEvent) => {
    for (const h of subscribers) h(ev);
  };
  return { client, transcribeSpy, emit };
}

let originalMediaRecorder: typeof MediaRecorder | undefined;
let originalGetUserMedia:
  | typeof navigator.mediaDevices.getUserMedia
  | undefined;
let activeStream: FakeMediaStream | null = null;
let getUserMediaShouldFail: { name: string } | null = null;

beforeEach(() => {
  FakeMediaRecorder.instances = [];
  activeStream = null;
  getUserMediaShouldFail = null;

  originalMediaRecorder = (globalThis as { MediaRecorder?: typeof MediaRecorder })
    .MediaRecorder;
  (globalThis as { MediaRecorder: unknown }).MediaRecorder =
    FakeMediaRecorder as unknown as typeof MediaRecorder;

  // happy-dom's navigator may not have mediaDevices; install a fake.
  const nav = navigator as unknown as {
    mediaDevices?: { getUserMedia?: typeof navigator.mediaDevices.getUserMedia };
  };
  if (!nav.mediaDevices) {
    Object.defineProperty(navigator, "mediaDevices", {
      value: {},
      configurable: true,
    });
  }
  originalGetUserMedia = nav.mediaDevices?.getUserMedia;
  nav.mediaDevices!.getUserMedia = vi.fn(async () => {
    if (getUserMediaShouldFail) {
      const err: Error & { name?: string } = new Error("denied");
      err.name = getUserMediaShouldFail.name;
      throw err;
    }
    activeStream = new FakeMediaStream();
    return activeStream as unknown as MediaStream;
  }) as unknown as typeof navigator.mediaDevices.getUserMedia;

  // Stub FileReader.readAsDataURL with a deterministic base64 payload.
  vi.stubGlobal(
    "FileReader",
    class {
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      result: string | null = null;
      readAsDataURL(_blob: Blob) {
        this.result = "data:audio/webm;base64,QUJDRA==";
        queueMicrotask(() => this.onload?.());
      }
    },
  );
});

afterEach(() => {
  if (originalMediaRecorder) {
    (globalThis as { MediaRecorder: unknown }).MediaRecorder =
      originalMediaRecorder;
  } else {
    delete (globalThis as { MediaRecorder?: unknown }).MediaRecorder;
  }
  const nav = navigator as unknown as {
    mediaDevices?: { getUserMedia?: typeof navigator.mediaDevices.getUserMedia };
  };
  if (nav.mediaDevices) {
    nav.mediaDevices.getUserMedia = originalGetUserMedia;
  }
  vi.unstubAllGlobals();
});

async function flushMicrotasks() {
  // The hook awaits a few microtasks to chain MediaRecorder.onstop ->
  // FileReader.onload. Two microtask boundaries are enough.
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("useTranscription", () => {
  it("emits the transcribe envelope and resolves on transcription_result", async () => {
    const { client, transcribeSpy, emit } = makeClient();
    const { result } = renderHook(() => useTranscription(client));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.recording).toBe(true);
    expect(FakeMediaRecorder.instances).toHaveLength(1);

    // Fake an audio chunk before the user stops recording.
    FakeMediaRecorder.instances[0].pushChunk(new Blob(["abc"]));

    let stopPromise!: Promise<string | null>;
    act(() => {
      stopPromise = result.current.stop();
    });

    await flushMicrotasks();

    // The hook should have submitted the envelope by now.
    expect(transcribeSpy).toHaveBeenCalledTimes(1);
    const [audioBase64, format, requestId] = transcribeSpy.mock.calls[0];
    expect(typeof requestId).toBe("string");
    expect(requestId.length).toBeGreaterThan(0);
    expect(audioBase64).toBe("QUJDRA==");
    expect(format).toBe("webm");

    // Reply with the matching request_id.
    act(() => {
      emit({
        event: "transcription_result",
        request_id: requestId,
        text: "hello world",
      });
    });

    const text = await stopPromise;
    expect(text).toBe("hello world");
    expect(result.current.recording).toBe(false);
    expect(result.current.transcribing).toBe(false);
    expect(result.current.error).toBeNull();
    // Mic tracks must be released so the OS indicator turns off.
    expect(activeStream?.tracks.every((t) => t.stopped)).toBe(true);
  });

  it("ignores transcription_result frames with a non-matching request_id", async () => {
    const { client, transcribeSpy, emit } = makeClient();
    const { result } = renderHook(() => useTranscription(client));

    await act(async () => {
      await result.current.start();
    });
    FakeMediaRecorder.instances[0].pushChunk(new Blob(["abc"]));

    let stopPromise!: Promise<string | null>;
    act(() => {
      stopPromise = result.current.stop();
    });
    await flushMicrotasks();

    expect(transcribeSpy).toHaveBeenCalledTimes(1);
    const requestId = transcribeSpy.mock.calls[0][2] as string;

    // Wrong request_id — must NOT resolve the promise.
    act(() => {
      emit({
        event: "transcription_result",
        request_id: `${requestId}-other`,
        text: "stale",
      });
    });

    let resolved = false;
    void stopPromise.then(() => {
      resolved = true;
    });
    await flushMicrotasks();
    expect(resolved).toBe(false);

    // Now the correct frame.
    act(() => {
      emit({
        event: "transcription_result",
        request_id: requestId,
        text: "actual",
      });
    });
    await expect(stopPromise).resolves.toBe("actual");
  });

  it("rejects to null + sets generic error on a chat-id-less error frame", async () => {
    const { client, transcribeSpy, emit } = makeClient();
    const { result } = renderHook(() => useTranscription(client));

    await act(async () => {
      await result.current.start();
    });
    FakeMediaRecorder.instances[0].pushChunk(new Blob(["abc"]));

    let stopPromise!: Promise<string | null>;
    act(() => {
      stopPromise = result.current.stop();
    });
    await flushMicrotasks();
    expect(transcribeSpy).toHaveBeenCalled();

    act(() => {
      emit({ event: "error", detail: "boom" });
    });

    const text = await stopPromise;
    expect(text).toBeNull();
    expect(result.current.error).toBe("generic");
    expect(result.current.transcribing).toBe(false);
  });

  it("times out after 30s with a generic error", async () => {
    vi.useFakeTimers();
    try {
      const { client, transcribeSpy } = makeClient();
      const { result } = renderHook(() => useTranscription(client));

      await act(async () => {
        await result.current.start();
      });
      FakeMediaRecorder.instances[0].pushChunk(new Blob(["abc"]));

      let stopPromise!: Promise<string | null>;
      act(() => {
        stopPromise = result.current.stop();
      });
      // Drain the queueMicrotask + readAsDataURL chain under fake timers.
      await vi.advanceTimersByTimeAsync(0);
      expect(transcribeSpy).toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(30_000);
      const text = await stopPromise;
      expect(text).toBeNull();
      expect(result.current.error).toBe("generic");
    } finally {
      vi.useRealTimers();
    }
  });

  it("flags a permission error when getUserMedia is denied", async () => {
    const { client } = makeClient();
    const { result } = renderHook(() => useTranscription(client));

    getUserMediaShouldFail = { name: "NotAllowedError" };

    await act(async () => {
      await expect(result.current.start()).rejects.toBeDefined();
    });
    expect(result.current.error).toBe("permission");
    expect(result.current.recording).toBe(false);
  });
});

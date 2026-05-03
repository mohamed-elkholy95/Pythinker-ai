import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";

import i18n from "@/i18n";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { ClientProvider } from "@/providers/ClientProvider";
import type { PythinkerClient } from "@/lib/pythinker-client";
import type { InboundEvent } from "@/lib/types";

/**
 * Phase 6 — live voice surface.
 *
 * The original Phase 5 / T11 case is preserved (composer rendered without a
 * ``ClientProvider`` ⇒ ``voiceEnabled`` is false ⇒ the mic stays disabled
 * with the legacy "not yet supported" tooltip). New cases mount the composer
 * inside a ``ClientProvider`` with ``voiceEnabled`` set to ``true`` and a
 * stub client whose ``transcribe`` is a spy.
 */
function wrap(children: React.ReactNode) {
  return <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state: "inactive" | "recording" = "inactive";
  mimeType = "audio/webm;codecs=opus";
  ondataavailable: ((ev: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(_stream: MediaStream) {
    FakeMediaRecorder.instances.push(this);
  }
  start() {
    this.state = "recording";
    queueMicrotask(() => this.ondataavailable?.({ data: new Blob(["abc"]) }));
  }
  stop() {
    this.state = "inactive";
    queueMicrotask(() => this.onstop?.());
  }
}

class FakeMediaStream {
  tracks = [{ stopped: false, stop() { this.stopped = true; } }];
  getTracks() {
    return this.tracks;
  }
}

function makeClientCtx() {
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

beforeEach(() => {
  FakeMediaRecorder.instances = [];
  originalMediaRecorder = (globalThis as { MediaRecorder?: typeof MediaRecorder })
    .MediaRecorder;
  (globalThis as { MediaRecorder: unknown }).MediaRecorder =
    FakeMediaRecorder as unknown as typeof MediaRecorder;

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
  nav.mediaDevices!.getUserMedia = vi.fn(
    async () => new FakeMediaStream() as unknown as MediaStream,
  ) as unknown as typeof navigator.mediaDevices.getUserMedia;

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
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("ThreadComposer × voice (disabled stub)", () => {
  it("renders a disabled mic button with the not-supported tooltip", () => {
    render(wrap(<ThreadComposer onSend={vi.fn()} variant="thread" />));

    const mic = screen.getByRole("button", { name: /voice input/i });
    expect(mic).toBeInTheDocument();
    expect(mic).toBeDisabled();
    expect(mic.getAttribute("aria-disabled")).toBe("true");
    expect(mic.getAttribute("title")).toBe("Voice input not yet supported");
  });
});

describe("ThreadComposer × voice (live, voice_enabled=true)", () => {
  function mountLive() {
    const { client, transcribeSpy, emit } = makeClientCtx();
    const view = render(
      wrap(
        <ClientProvider client={client} token="t" voiceEnabled={true}>
          <ThreadComposer onSend={vi.fn()} variant="thread" />
        </ClientProvider>,
      ),
    );
    return { ...view, client, transcribeSpy, emit };
  }

  it("renders an enabled mic button when voiceEnabled is true", () => {
    mountLive();
    const mic = screen.getByRole("button", { name: /voice input/i });
    expect(mic).not.toBeDisabled();
    expect(mic.getAttribute("title")).toBe("Voice input");
  });

  it("first click starts recording, second click submits and appends transcript", async () => {
    const { transcribeSpy, emit } = mountLive();

    const mic = screen.getByRole("button", { name: /voice input/i });

    // Click 1: begin recording. ``handleMicClick`` awaits ``start()`` (which
    // awaits ``getUserMedia``), so we drain a few microtasks before asserting.
    await act(async () => {
      fireEvent.click(mic);
      for (let i = 0; i < 8; i += 1) await flushMicrotasks();
    });

    // Recording UI cue must show the dot indicator + recording tooltip.
    expect(mic.getAttribute("data-recording")).toBe("true");
    expect(screen.getByTestId("voice-recording-dot")).toBeInTheDocument();

    // Click 2: stop -> hook submits envelope. The click handler is a
    // suspended async function; we drain microtasks to let
    // MediaRecorder.onstop -> FileReader.onload -> client.transcribe +
    // onTranscription registration settle before we emit the reply.
    await act(async () => {
      fireEvent.click(mic);
      for (let i = 0; i < 8; i += 1) await flushMicrotasks();
    });

    expect(transcribeSpy).toHaveBeenCalledTimes(1);
    const [, format, requestId] = transcribeSpy.mock.calls[0];
    expect(format).toBe("webm");

    await act(async () => {
      emit({
        event: "transcription_result",
        request_id: requestId as string,
        text: "spoken words",
      });
      // Allow the suspended click handler to resume and apply ``setValue``.
      for (let i = 0; i < 8; i += 1) await flushMicrotasks();
    });

    const textarea = screen.getByLabelText(
      /message input/i,
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe("spoken words ");
  });
});

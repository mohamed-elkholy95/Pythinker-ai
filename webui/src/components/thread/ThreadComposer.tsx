import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  ArrowUp,
  ImageIcon,
  Loader2,
  Mic,
  Paperclip,
  Square,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { CommandPalette } from "@/components/CommandPalette";
import { ModelSwitcher } from "@/components/ModelSwitcher";
import {
  useAttachedImages,
  type AttachedImage,
  type AttachmentError,
  type UseAttachedImagesApi,
  MAX_IMAGES_PER_MESSAGE,
} from "@/hooks/useAttachedImages";
import { useClipboardAndDrop } from "@/hooks/useClipboardAndDrop";
import { useCommands } from "@/hooks/useCommands";
import type { SendImage } from "@/hooks/usePythinkerStream";
import { useTranscription } from "@/hooks/useTranscription";
import type { ModelRow } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useOptionalClient } from "@/providers/ClientProvider";

/** ``<input accept>``: aligned with the server's MIME whitelist. SVG is
 * deliberately excluded to avoid an embedded-script XSS surface. */
const ACCEPT_ATTR = "image/png,image/jpeg,image/webp,image/gif";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

interface ThreadComposerProps {
  onSend: (content: string, images?: SendImage[]) => void;
  disabled?: boolean;
  placeholder?: string;
  modelLabel?: string | null;
  variant?: "thread" | "hero";
  isStreaming?: boolean;
  onStop?: () => void;
  /** Configured model list from ``/api/models``. When non-empty along with
   * ``currentModel``, the read-only label is replaced by an inline switcher. */
  models?: ModelRow[];
  /** Configured default for the current chat (no override applied). */
  currentModel?: string | null;
  /** Per-chat override; null means "use default". */
  override?: string | null;
  /** Pass empty string to clear the override. */
  onModelChange?: (modelOrEmpty: string) => void;
  /** Hoisted ``useAttachedImages`` instance from the parent. When provided,
   * the composer reuses that handle so callers (e.g. ``ThreadShell``) can
   * attach a drop zone above the composer and still feed the same staged
   * state. When omitted, the composer falls back to its own internal hook
   * — preserving standalone usage in tests and any legacy embedders. */
  attachedImages?: UseAttachedImagesApi;
}

export function ThreadComposer({
  onSend,
  disabled,
  placeholder,
  modelLabel = null,
  variant = "thread",
  isStreaming = false,
  onStop,
  models,
  currentModel = null,
  override = null,
  onModelChange,
  attachedImages,
}: ThreadComposerProps) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [inlineError, setInlineError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const chipRefs = useRef(new Map<string, HTMLButtonElement>());
  const isHero = variant === "hero";
  const resolvedPlaceholder =
    placeholder ?? t("thread.composer.placeholderThread");

  // Hooks must run unconditionally — call the internal handle every render
  // and only swap in the parent-provided one when the caller hoisted it.
  // The unused internal state is cheap (no encoder runs without a file).
  const internalAttached = useAttachedImages();
  const { images, enqueue, remove, clear, encoding, full } =
    attachedImages ?? internalAttached;

  // Slash-command palette opens when the textarea content starts with ``/``
  // and contains no whitespace yet. The ``useCommands`` fetch only runs while
  // the palette is mounted (see ``SlashCommandPalette`` below) so composer
  // tests that don't supply a ``ClientProvider`` continue to render fine.
  const paletteOpen =
    value.startsWith("/") && !value.includes(" ") && !value.includes("\n");

  const formatRejection = useCallback(
    (reason: AttachmentError): string => {
      const key = `thread.composer.imageRejected.${reason}`;
      return t(key, { max: MAX_IMAGES_PER_MESSAGE });
    },
    [t],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const { rejected } = enqueue(files);
      if (rejected.length > 0) {
        setInlineError(formatRejection(rejected[0].reason));
      } else {
        setInlineError(null);
      }
    },
    [enqueue, formatRejection],
  );

  // Drop handling lives on the parent ``ThreadShell`` so users can drop
  // files anywhere on the chat surface; here we only need ``onPaste`` for
  // the textarea — paste is a textarea-scoped event and would never reach
  // a parent ``onPaste`` listener anyway.
  const { onPaste } = useClipboardAndDrop(addFiles);

  useEffect(() => {
    if (disabled) return;
    const el = textareaRef.current;
    if (!el) return;
    const id = requestAnimationFrame(() => el.focus());
    return () => cancelAnimationFrame(id);
  }, [disabled]);

  const readyImages = useMemo(
    () => images.filter((img): img is AttachedImage & { dataUrl: string } =>
      img.status === "ready" && typeof img.dataUrl === "string",
    ),
    [images],
  );
  const hasErrors = images.some((img) => img.status === "error");

  const canSend =
    !disabled
    && !encoding
    && !hasErrors
    && (value.trim().length > 0 || readyImages.length > 0);

  const submit = useCallback(() => {
    if (!canSend) return;
    const trimmed = value.trim();
    // Share the same normalized ``data:`` URL with both the wire payload and
    // the optimistic bubble preview: data URLs are self-contained (no blob
    // lifetime, safe under React StrictMode double-mount) and keep the
    // bubble in sync with whatever the backend actually sees.
    const payload: SendImage[] | undefined =
      readyImages.length > 0
        ? readyImages.map((img) => ({
            media: {
              data_url: img.dataUrl,
              name: img.file.name,
            },
            preview: { url: img.dataUrl, name: img.file.name },
          }))
        : undefined;
    onSend(trimmed, payload);
    setValue("");
    setInlineError(null);
    // Bubble owns the data URL copy; safe to revoke every staged blob
    // preview here without affecting the rendered message.
    clear();
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.style.height = "auto";
        el.focus();
      }
    });
  }, [canSend, clear, onSend, readyImages, value]);

  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // While the slash palette is open, forward navigation keys to its
    // listbox. Focus stays in the textarea (caret keeps blinking where the
    // user is typing) so we never have to tab into the popover.
    if (paletteOpen) {
      const forwardKeys = new Set([
        "ArrowDown",
        "ArrowUp",
        "Enter",
        "Tab",
        "Escape",
      ]);
      if (forwardKeys.has(e.key)) {
        if (
          e.key === "Enter"
          && (e.shiftKey || e.nativeEvent.isComposing)
        ) {
          // Shift+Enter / IME composition still belongs to the textarea.
          return;
        }
        e.preventDefault();
        const list = document.querySelector('[role="listbox"]');
        list?.dispatchEvent(
          new KeyboardEvent("keydown", { key: e.key, bubbles: true }),
        );
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const onInput: React.FormEventHandler<HTMLTextAreaElement> = (e) => {
    const el = e.currentTarget;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
  };

  const onFilePick: React.ChangeEventHandler<HTMLInputElement> = (e) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    addFiles(files);
  };

  const removeChip = useCallback(
    (id: string) => {
      const { nextFocusId } = remove(id);
      setInlineError(null);
      requestAnimationFrame(() => {
        const el = nextFocusId ? chipRefs.current.get(nextFocusId) : null;
        if (el) {
          el.focus();
        } else {
          textareaRef.current?.focus();
        }
      });
    },
    [remove],
  );

  const onChipKey = useCallback(
    (id: string) => (e: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (
        e.key === "Delete" ||
        e.key === "Backspace" ||
        e.key === "Enter" ||
        e.key === " "
      ) {
        e.preventDefault();
        removeChip(id);
      }
    },
    [removeChip],
  );

  const attachButtonDisabled = disabled || full;

  // Voice input is gated on the server-side ``voice_enabled`` flag (plumbed
  // through ``ClientProvider``). When enabled, the mic button drives the
  // ``useTranscription`` hook; otherwise it stays disabled with the legacy
  // "not yet supported" tooltip. ``useOptionalClient`` lets the standalone
  // composer tests render without a provider.
  const ctx = useOptionalClient();
  const voiceEnabled = ctx?.voiceEnabled ?? false;
  // Always call the hook to satisfy React's rules of hooks; pass ``null``
  // when the composer is rendered outside a ``ClientProvider`` (legacy
  // standalone tests). ``stop()`` is a no-op on a null client.
  const transcription = useTranscription(ctx?.client ?? null);
  const voiceButtonDisabled = disabled || !voiceEnabled;
  const voiceTooltip = voiceEnabled
    ? transcription.recording
      ? t("voice.recording")
      : transcription.transcribing
        ? t("voice.transcribing")
        : transcription.error === "permission"
          ? t("voice.errorPermission")
          : transcription.error === "generic"
            ? t("voice.errorGeneric")
            : t("voice.button")
    : t("voice.notSupported");

  const handleMicClick = useCallback(async () => {
    if (!voiceEnabled || disabled) return;
    if (transcription.recording) {
      const text = await transcription.stop();
      if (text && text.length > 0) {
        setValue((prev) => `${prev}${text} `);
        requestAnimationFrame(() => {
          const el = textareaRef.current;
          if (el) {
            el.style.height = "auto";
            el.style.height = `${Math.min(el.scrollHeight, 260)}px`;
            el.focus();
          }
        });
      } else if (transcription.error) {
        // Surface enough information for users without spamming a toast UI
        // we don't yet have. Tooltip + console keeps the failure visible.
        // eslint-disable-next-line no-console
        console.warn(
          `[voice] transcription failed (${transcription.error})`,
        );
      }
      return;
    }
    try {
      await transcription.start();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("[voice] start failed", err);
    }
  }, [voiceEnabled, disabled, transcription]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className={cn("w-full", isHero ? "px-0" : "px-1 pb-1.5 pt-1 sm:px-0")}
    >
      <div
        className={cn(
          "relative mx-auto flex w-full flex-col overflow-hidden transition-all duration-200",
          isHero
            ? "max-w-[40rem] rounded-[24px] border border-border/75 bg-card/72 shadow-[0_10px_30px_rgba(0,0,0,0.10)]"
            : "max-w-[49.5rem] rounded-[16px] border border-border/70 bg-card/55",
          "focus-within:bg-card/70 focus-within:ring-1 focus-within:ring-foreground/8",
          disabled && "opacity-60",
        )}
      >
        {images.length > 0 ? (
          <div
            className="flex flex-wrap gap-2 px-3 pt-3"
            aria-label={t("thread.composer.attachImage")}
          >
            {images.map((img) => (
              <AttachmentChip
                key={img.id}
                image={img}
                labelRemove={t("thread.composer.remove")}
                labelEncoding={t("thread.composer.encoding")}
                normalizedHint={(orig, current) =>
                  t("thread.composer.normalizedSizeHint", {
                    orig: formatBytes(orig),
                    current: formatBytes(current),
                  })
                }
                formatError={formatRejection}
                onRemove={() => removeChip(img.id)}
                onKeyDown={onChipKey(img.id)}
                registerRef={(el) => {
                  if (el) chipRefs.current.set(img.id, el);
                  else chipRefs.current.delete(img.id);
                }}
              />
            ))}
          </div>
        ) : null}
        {paletteOpen ? (
          <SlashCommandPalette
            query={value.slice(1)}
            onSelect={(name) => {
              setValue(`${name} `);
              requestAnimationFrame(() => textareaRef.current?.focus());
            }}
            onClose={() => {
              // Closing without selection: append a space so the palette
              // predicate (no whitespace) is no longer satisfied. This
              // preserves the user's typed slash without trapping them.
              setValue(`${value} `);
            }}
            anchorRef={textareaRef}
          />
        ) : null}
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onInput={onInput}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          rows={1}
          placeholder={resolvedPlaceholder}
          disabled={disabled}
          aria-label={t("thread.composer.inputAria")}
          className={cn(
            "w-full resize-none bg-transparent",
            isHero
              ? "min-h-[96px] px-4 pb-2 pt-4 text-[15px] leading-6"
              // Thread variant: 16px on touch to suppress iOS zoom-on-focus,
              // downshifted to 14px on `sm:` and above where space matters.
              : "min-h-[50px] px-4 pb-1.5 pt-3 text-base sm:text-sm",
            "placeholder:text-muted-foreground",
            "focus:outline-none focus-visible:outline-none",
            "disabled:cursor-not-allowed",
          )}
        />
        {inlineError ? (
          <div
            role="alert"
            className={cn(
              "mx-3 mb-1 rounded-md border border-destructive/40 bg-destructive/8 px-2.5 py-1",
              "text-[11.5px] font-medium text-destructive",
            )}
          >
            {inlineError}
          </div>
        ) : null}
        <div
          className={cn(
            "flex items-center justify-between gap-2",
            isHero ? "px-3.5 pb-3.5" : "px-3 pb-2",
          )}
        >
          <div className="flex min-w-0 items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPT_ATTR}
              multiple
              hidden
              onChange={onFilePick}
            />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              disabled={attachButtonDisabled}
              aria-label={t("thread.composer.attachImage")}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                "rounded-full text-muted-foreground hover:text-foreground",
                // 44x44 on touch (Apple HIG / WCAG 2.5.5); compact at sm+.
                isHero
                  ? "h-11 w-11 sm:h-8.5 sm:w-8.5"
                  : "h-11 w-11 sm:h-7.5 sm:w-7.5",
              )}
            >
              <Paperclip className={cn(isHero ? "h-4 w-4" : "h-3.5 w-3.5")} />
            </Button>
            {/* Voice input — when ``voiceEnabled`` is false the button is a
                disabled stub (preserves the no-provider standalone tests).
                When enabled, click toggles MediaRecorder + transcription via
                ``useTranscription``. */}
            <Button
              type="button"
              size="icon"
              variant="ghost"
              disabled={voiceButtonDisabled}
              aria-disabled={voiceButtonDisabled}
              aria-label={t("voice.button")}
              aria-pressed={transcription.recording}
              data-recording={transcription.recording ? "true" : undefined}
              data-transcribing={transcription.transcribing ? "true" : undefined}
              title={voiceTooltip}
              onClick={handleMicClick}
              className={cn(
                "relative rounded-full text-muted-foreground hover:text-foreground",
                // Match attach button sizing for visual rhythm in the row.
                isHero
                  ? "h-11 w-11 sm:h-8.5 sm:w-8.5"
                  : "h-11 w-11 sm:h-7.5 sm:w-7.5",
                !voiceEnabled && "opacity-60",
                transcription.recording && "text-destructive",
              )}
            >
              <Mic className={cn(isHero ? "h-4 w-4" : "h-3.5 w-3.5")} />
              {transcription.recording ? (
                <span
                  aria-hidden
                  data-testid="voice-recording-dot"
                  className={cn(
                    "absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-destructive",
                    "motion-safe:animate-pulse",
                  )}
                />
              ) : null}
              {transcription.transcribing ? (
                <span
                  aria-hidden
                  data-testid="voice-transcribing-spinner"
                  className="absolute inset-0 grid place-items-center"
                >
                  <Loader2
                    className={cn(
                      "animate-spin motion-reduce:animate-none",
                      isHero ? "h-3 w-3" : "h-2.5 w-2.5",
                    )}
                  />
                </span>
              ) : null}
            </Button>
            {models && models.length > 0 && currentModel && onModelChange ? (
              <ModelSwitcher
                models={models}
                currentModel={currentModel}
                override={override}
                onChange={onModelChange}
              />
            ) : modelLabel ? (
              /* Fallback for environments where the switcher isn't wired
                 (legacy callers, /api/models 503s, no currentModel resolved). */
              <span
                title={modelLabel}
                className={cn(
                  "inline-flex min-w-0 items-center gap-1.5 rounded-full border px-2.5 py-1",
                  "border-foreground/10 bg-foreground/[0.035] font-medium text-foreground/80",
                  isHero ? "text-[11px]" : "text-[10.5px]",
                )}
              >
                <span
                  aria-hidden
                  className="h-1.5 w-1.5 flex-none rounded-full bg-emerald-500/80"
                />
                <span className="truncate">{modelLabel}</span>
              </span>
            ) : null}
            <span className="hidden select-none text-[10.5px] text-muted-foreground/60 sm:inline">
              {t("thread.composer.sendHint")}
            </span>
          </div>
          <span className="sm:hidden" aria-hidden />
          {isStreaming && onStop ? (
            <Button
              type="button"
              size="icon"
              onClick={onStop}
              aria-label={t("actions.stop")}
              className={cn(
                "rounded-full border border-border/60 bg-destructive/15 text-destructive shadow-none transition-transform hover:bg-destructive/25",
                // 44x44 on touch; compact at sm+.
                isHero
                  ? "h-11 w-11 sm:h-8.5 sm:w-8.5"
                  : "h-11 w-11 sm:h-7.5 sm:w-7.5",
                "hover:scale-[1.03] active:scale-95",
              )}
            >
              <Square
                className={cn("fill-current", isHero ? "h-3.5 w-3.5" : "h-3 w-3")}
              />
            </Button>
          ) : (
            <Button
              type="submit"
              size="icon"
              disabled={!canSend}
              aria-label={t("thread.composer.send")}
              className={cn(
                "rounded-full border border-border/70 bg-secondary/85 text-secondary-foreground shadow-none transition-transform hover:bg-accent",
                // 44x44 on touch; compact at sm+.
                isHero
                  ? "h-11 w-11 sm:h-8.5 sm:w-8.5"
                  : "h-11 w-11 sm:h-7.5 sm:w-7.5",
                canSend && "hover:scale-[1.03] active:scale-95",
              )}
            >
              <ArrowUp className={cn(isHero ? "h-4.5 w-4.5" : "h-4 w-4")} />
            </Button>
          )}
        </div>
      </div>
    </form>
  );
}

interface SlashCommandPaletteProps {
  query: string;
  onSelect: (name: string) => void;
  onClose: () => void;
  anchorRef: React.RefObject<HTMLTextAreaElement>;
}

/**
 * Thin wrapper that mounts ``useCommands`` only while the palette is open.
 *
 * ``useCommands`` calls ``useClient`` which throws when no ``ClientProvider``
 * is in the tree. By keeping the hook scoped to this conditionally-mounted
 * subcomponent, ``ThreadComposer`` can still render under the legacy unit
 * tests that exercise it standalone — those tests never type a ``/`` so the
 * subcomponent is never instantiated.
 */
function SlashCommandPalette({
  query,
  onSelect,
  onClose,
  anchorRef,
}: SlashCommandPaletteProps) {
  const { commands } = useCommands();
  return (
    <CommandPalette
      open
      commands={commands}
      query={query}
      onSelect={onSelect}
      onClose={onClose}
      anchorRef={anchorRef as React.RefObject<HTMLElement>}
    />
  );
}

interface AttachmentChipProps {
  image: AttachedImage;
  labelRemove: string;
  labelEncoding: string;
  normalizedHint: (origBytes: number, currentBytes: number) => string;
  formatError: (reason: AttachmentError) => string;
  onRemove: () => void;
  onKeyDown: (e: ReactKeyboardEvent<HTMLButtonElement>) => void;
  registerRef: (el: HTMLButtonElement | null) => void;
}

function AttachmentChip({
  image,
  labelRemove,
  labelEncoding,
  normalizedHint,
  formatError,
  onRemove,
  onKeyDown,
  registerRef,
}: AttachmentChipProps) {
  const sizeLabel =
    image.status === "ready" && image.normalized && image.encodedBytes
      ? normalizedHint(image.file.size, image.encodedBytes)
      : formatBytes(image.file.size);
  const tone =
    image.status === "error"
      ? "border-destructive/40 bg-destructive/5 text-destructive"
      : "border-border/70 bg-muted/60";

  return (
    <div
      className={cn(
        "group relative flex items-center gap-2 rounded-[12px] border px-2 py-1.5",
        "transition-colors motion-reduce:transition-none",
        tone,
      )}
      data-testid="composer-chip"
    >
      <div className="relative h-10 w-10 overflow-hidden rounded-md bg-background">
        {image.previewUrl ? (
          <img
            src={image.previewUrl}
            alt=""
            aria-hidden
            loading="eager"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <ImageIcon className="h-4 w-4 text-muted-foreground" aria-hidden />
          </div>
        )}
        {image.status === "encoding" ? (
          <div
            className="absolute inset-0 flex items-center justify-center bg-background/60"
            aria-label={labelEncoding}
          >
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />
          </div>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col text-[11.5px] leading-4">
        <span className="truncate max-w-[14rem] font-medium" title={image.file.name}>
          {image.file.name}
        </span>
        <span className="truncate text-muted-foreground">
          {image.status === "error" && image.error
            ? formatError(image.error)
            : sizeLabel}
        </span>
      </div>
      <button
        type="button"
        ref={registerRef}
        onClick={onRemove}
        onKeyDown={onKeyDown}
        aria-label={labelRemove}
        className={cn(
          // 44x44 on touch; compact at sm+ to keep the chip layout tight.
          "ml-1 grid h-11 w-11 sm:h-5 sm:w-5 flex-none place-items-center rounded-full",
          "text-muted-foreground/80 hover:bg-foreground/8 hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-foreground/30",
        )}
      >
        <X className="h-3.5 w-3.5" aria-hidden />
      </button>
    </div>
  );
}

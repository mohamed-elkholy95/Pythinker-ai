import { MessageBubble } from "@/components/MessageBubble";
import { extractThinkBlocks } from "@/lib/extractThinkBlocks";
import type { UIMessage } from "@/lib/types";

interface ThreadMessagesProps {
  messages: UIMessage[];
  /** Re-run the last assistant turn from the trailing user message. */
  onRegenerate?: () => void;
  /** Rewrite a user bubble in place and resubmit from there. */
  onEdit?: (messageId: string, newContent: string) => void;
}

/**
 * Tool-pivot assistant turns persist with only ``<think>...</think>`` content
 * (the model emitted reasoning + a tool call, no user-facing answer). The
 * MessageBubble returns ``null`` for those, but a ``null`` child still keeps
 * its wrapper div in the flex layout and contributes a ``gap-5`` slot —
 * stacking 80–120 px of phantom whitespace per pivot. Drop those messages
 * here so the layout never even allocates a row.
 */
function isRenderable(message: UIMessage): boolean {
  if (message.role !== "assistant" || message.kind === "trace") return true;
  if (message.isStreaming) return true;
  const visible = extractThinkBlocks(message.content).visible.trim();
  return visible.length > 0;
}

export function ThreadMessages({
  messages,
  onRegenerate,
  onEdit,
}: ThreadMessagesProps) {
  const renderable = messages.filter(isRenderable);
  return (
    <div className="flex w-full flex-col gap-3">
      {renderable.map((message, index) => (
        <div
          key={message.id}
          data-message-index={index}
          className="rounded-md"
        >
          <MessageBubble
            message={message}
            onRegenerate={onRegenerate}
            onEdit={onEdit}
          />
        </div>
      ))}
    </div>
  );
}

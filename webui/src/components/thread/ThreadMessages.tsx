import { MessageBubble } from "@/components/MessageBubble";
import type { UIMessage } from "@/lib/types";

interface ThreadMessagesProps {
  messages: UIMessage[];
  /** Re-run the last assistant turn from the trailing user message. */
  onRegenerate?: () => void;
  /** Rewrite a user bubble in place and resubmit from there. */
  onEdit?: (messageId: string, newContent: string) => void;
}

export function ThreadMessages({
  messages,
  onRegenerate,
  onEdit,
}: ThreadMessagesProps) {
  return (
    <div className="flex w-full flex-col gap-5">
      {messages.map((message, index) => (
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

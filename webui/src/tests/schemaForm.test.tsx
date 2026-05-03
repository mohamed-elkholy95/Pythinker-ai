import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SchemaForm } from "@/components/admin/config/SchemaForm";

const schema = {
  type: "object",
  properties: {
    enable: { type: "boolean", title: "Enable" },
    model: { type: "string", title: "Model" },
    level: { type: "string", enum: ["INFO", "DEBUG"], title: "Level" },
    retries: { type: "integer", minimum: 0, maximum: 5, title: "Retries" },
    allowFrom: { type: "array", items: { type: "string" }, title: "Allow From" },
    extraHeaders: {
      type: "object",
      additionalProperties: { type: "string" },
      title: "Extra Headers",
    },
    timeout: { anyOf: [{ type: "integer", minimum: 1 }, { type: "null" }], title: "Timeout" },
    apiKey: { type: "string", title: "API Key" },
  },
};

describe("SchemaForm", () => {
  it("renders schema-backed fields and canonicalizes snake_case paths", async () => {
    const stage = vi.fn();
    render(
      <SchemaForm
        schemaNode={schema}
        value={{ enable: false, model: "openai/gpt", level: "INFO", retries: 2, allowFrom: [] }}
        displayPath="providers.openai"
        canonicalPath="providers.openai"
        secretPaths={["providers.openai.api_key"]}
        onStage={stage}
        onReplaceSecret={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("switch", { name: "Enable" }));
    expect(stage).toHaveBeenCalledWith("providers.openai.enable", true);
    expect(screen.getByRole("button", { name: /replace secret/i })).toBeInTheDocument();
  });

  it("edits dynamic maps and nullable fields", async () => {
    const stage = vi.fn();
    render(
      <SchemaForm
        schemaNode={schema}
        value={{ extraHeaders: { "X-Trace": "enabled" }, timeout: 30 }}
        displayPath="providers.openai"
        canonicalPath="providers.openai"
        secretPaths={[]}
        onStage={stage}
        onReplaceSecret={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /add extra headers entry/i }));
    await userEvent.type(screen.getByLabelText(/extra headers key/i), "X-Mode");
    await userEvent.type(screen.getByLabelText(/extra headers value/i), "safe");
    await userEvent.click(screen.getByRole("button", { name: /save extra headers entry/i }));
    expect(stage).toHaveBeenCalledWith("providers.openai.extra_headers", {
      "X-Trace": "enabled",
      "X-Mode": "safe",
    });

    await userEvent.click(screen.getByRole("button", { name: /clear timeout/i }));
    expect(stage).toHaveBeenCalledWith("providers.openai.timeout", null);
  });
});

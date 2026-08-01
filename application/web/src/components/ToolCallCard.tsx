/**
 * Copyright 2026 Amazon.com, Inc. or its affiliates
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { ToolEvent } from "../types";

interface Props {
  event: ToolEvent;
}

function formatToolInput(input: unknown): string {
  if (input === undefined || input === null) {
    return "(매개변수 없음)";
  }
  if (typeof input === "object" && !Array.isArray(input)) {
    const keys = Object.keys(input as Record<string, unknown>);
    if (keys.length === 0) {
      return "(매개변수 없음 — 기본값 사용)";
    }
  }
  return JSON.stringify(input, null, 2);
}

/** Make tool result / info payloads readable across multiple lines. */
function formatToolPayload(data: string | undefined): string {
  if (!data) return "";

  try {
    const parsed = JSON.parse(data) as unknown;
    if (Array.isArray(parsed)) {
      const texts = parsed
        .filter(
          (block): block is { type: string; text: string } =>
            !!block &&
            typeof block === "object" &&
            (block as { type?: unknown }).type === "text" &&
            typeof (block as { text?: unknown }).text === "string",
        )
        .map((block) => block.text);
      if (texts.length > 0) {
        return texts.join("\n\n");
      }
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    // Python-style repr often embeds literal \n / \t escape sequences.
    return data.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
  }
}

export function ToolCallCard({ event }: Props) {
  if (event.type === "tool") {
    return (
      <details className="tool-card">
        <summary>Tool: {event.tool}</summary>
        <pre>{formatToolInput(event.input)}</pre>
      </details>
    );
  }
  if (event.type === "tool_result") {
    const label = event.tool ? `Tool result: ${event.tool}` : "Tool result";
    return (
      <details className="tool-card">
        <summary>{label}</summary>
        <pre>{formatToolPayload(event.data)}</pre>
      </details>
    );
  }
  return (
    <details className="tool-card">
      <summary>Info</summary>
      <pre>{formatToolPayload(event.data)}</pre>
    </details>
  );
}

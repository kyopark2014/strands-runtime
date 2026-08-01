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

import { randomUUID } from "../randomUUID";
import type { Message } from "../types";

/** Keep optimistic `pending-*` ids when server rows match, to avoid remount flicker. */
export function stabilizeMessageKeys(prev: Message[], next: Message[]): Message[] {
  if (prev.length === 0) return next;
  const used = new Set<string>();
  return next.map((msg) => {
    const match = prev.find(
      (prevMsg) =>
        !used.has(prevMsg.id) &&
        prevMsg.id.startsWith("pending") &&
        prevMsg.role === msg.role &&
        prevMsg.content === msg.content,
    );
    if (!match) return msg;
    used.add(match.id);
    return { ...msg, id: match.id };
  });
}

export function buildDisplayPrompt(prompt: string, files: string[]): string {
  return prompt.trim() || (files.length > 0 ? "첨부한 이미지를 분석해주세요." : "");
}

export function buildOptimisticUserMessage(
  taskId: string,
  content: string,
  files: string[],
): Message {
  return {
    id: `pending-${randomUUID()}`,
    task_id: taskId,
    role: "user",
    content,
    images: files,
    tool_events: [],
    created_at: new Date().toISOString(),
  };
}

export function buildRagUploadNotice(taskId: string, message: string): Message {
  return {
    id: `rag-upload-${randomUUID()}`,
    task_id: taskId,
    role: "assistant",
    content: message,
    images: [],
    tool_events: [],
    created_at: new Date().toISOString(),
  };
}

export function buildPendingAssistantMessage(
  taskId: string,
  content: string,
  images: string[],
  toolEvents: Message["tool_events"],
): Message {
  return {
    id: `pending-assistant-${randomUUID()}`,
    task_id: taskId,
    role: "assistant",
    content,
    images,
    tool_events: toolEvents,
    created_at: new Date().toISOString(),
  };
}

export function shouldAppendAssistantMessage(
  final: {
    content?: string;
    tool_events: Message["tool_events"];
  } | null | undefined,
): boolean {
  return Boolean(final && (final.content || final.tool_events.length > 0));
}

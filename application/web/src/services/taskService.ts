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

import type { AppConfig, Task } from "../types";
import type { CreateTaskDefaults } from "./appDataService";

export const MAX_TASK_TITLE_LENGTH = 50;

export function sortTasks(tasks: Task[]): Task[] {
  return [...tasks].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

export function titleFromPrompt(prompt: string): string {
  return prompt.trim().slice(0, MAX_TASK_TITLE_LENGTH) || "New task";
}

export function buildNewTaskDefaults(
  config: AppConfig,
  activeTask: Task | null,
): CreateTaskDefaults {
  return {
    model_name: activeTask?.model_name ?? config.default_model,
    skills: activeTask?.skills ?? config.default_skills,
    mcp_servers: activeTask?.mcp_servers ?? config.default_mcp_servers,
    strands_tools: activeTask?.strands_tools ?? config.default_strands_tools,
    guardrail_enabled: activeTask?.guardrail_enabled ?? false,
    memory_enabled: activeTask?.memory_enabled ?? true,
  };
}

export function buildFallbackTaskDefaults(config: AppConfig): CreateTaskDefaults {
  return {
    model_name: config.default_model,
    skills: config.default_skills,
    mcp_servers: config.default_mcp_servers,
    strands_tools: config.default_strands_tools,
    memory_enabled: true,
  };
}

export function applyTaskTitleFromPrompt(
  tasks: Task[],
  taskId: string,
  prompt: string,
): Task[] {
  return sortTasks(
    tasks.map((task) =>
      task.id === taskId && (task.title === "New task" || !task.title)
        ? {
            ...task,
            title: titleFromPrompt(prompt),
            updated_at: new Date().toISOString(),
          }
        : task,
    ),
  );
}

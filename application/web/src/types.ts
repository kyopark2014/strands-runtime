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

export interface Task {
  id: string;
  user_id: string;
  title: string;
  runtime_session_id: string;
  model_name: string;
  skills: string[];
  mcp_servers: string[];
  strands_tools: string[];
  guardrail_enabled: boolean;
  memory_enabled: boolean;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ToolEvent {
  type: "text" | "tool" | "tool_result" | "info";
  tool?: string;
  input?: unknown;
  toolUseId?: string;
  data?: string;
}

export interface Message {
  id: string;
  task_id: string;
  role: "user" | "assistant";
  content: string;
  images: string[];
  tool_events: ToolEvent[];
  created_at: string;
}

export interface AppConfig {
  projectName: string;
  skills: string[];
  mcp_servers: string[];
  strands_tools: string[];
  models: string[];
  default_model: string;
  default_skills: string[];
  default_mcp_servers: string[];
  default_strands_tools: string[];
}

export interface StreamEvent {
  type: "token" | "text" | "tool" | "tool_result" | "info" | "done" | "error";
  data?: string;
  content?: string;
  images?: string[];
  tool_events?: ToolEvent[];
  tool?: string;
  input?: unknown;
  toolUseId?: string;
}

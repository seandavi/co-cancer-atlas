// /api/chat — AI SDK streaming endpoint. Reads UIMessages from the
// client, runs streamText with Gemini + the atlas tool registry,
// streams back the UI message stream.

import {
  convertToModelMessages,
  stepCountIs,
  streamText,
  type UIMessage,
} from "ai";
import { google } from "@ai-sdk/google";

import { buildSystemPrompt } from "@/app/lib/prompts/system";
import { atlasTools } from "@/app/lib/tools";

export const runtime = "nodejs";
export const maxDuration = 60;

// gemini-2.5-flash: fast, cheap, tool-calling capable. Swap to pro
// for harder reasoning if needed.
const MODEL_ID = "gemini-2.5-flash";
const MAX_STEPS = 24;

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  const result = streamText({
    model: google(MODEL_ID),
    system: buildSystemPrompt({ maxSteps: MAX_STEPS }),
    messages: await convertToModelMessages(messages),
    tools: atlasTools,
    stopWhen: stepCountIs(MAX_STEPS),
  });

  return result.toUIMessageStreamResponse();
}

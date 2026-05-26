"use client";

// Chat UI: messages list + composer. Uses @ai-sdk/react useChat to
// stream from /api/chat. Tool calls render inline via ToolResult.

import { useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import ToolResult from "./ToolResult";

const STARTER_PROMPTS = [
  "Show me a map of all-sites cancer incidence across Colorado counties.",
  "What are the top 5 counties by lung cancer mortality?",
  "Plot the relationship between adult smoking and lung cancer incidence.",
  "Which counties have the highest food-desert tract rates?",
];

export default function Chat() {
  const { messages, sendMessage, status, error, stop } = useChat({
    transport: new DefaultChatTransport({ api: "/api/chat" }),
  });
  const [input, setInput] = useState("");

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed) return;
    sendMessage({ text: trimmed });
    setInput("");
  };

  const busy = status === "submitted" || status === "streaming";

  return (
    <div className="min-h-screen flex flex-col bg-slate-50 dark:bg-slate-950">
      <header className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-baseline justify-between">
          <div>
            <h1 className="text-base font-semibold text-slate-900 dark:text-slate-50">
              Colorado Cancer Atlas
            </h1>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Chat with the public ECCO API · DuckDB + Gemini
            </p>
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
          {messages.length === 0 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600 dark:text-slate-400">
                Ask about cancer incidence and mortality, screening rates,
                or socio-demographic context across Colorado counties and
                census tracts. Try one of these to start:
              </p>
              <ul className="space-y-2">
                {STARTER_PROMPTS.map((p) => (
                  <li key={p}>
                    <button
                      type="button"
                      className="text-left text-sm rounded-md border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 px-3 py-2 hover:border-slate-300 dark:hover:border-slate-700 w-full"
                      onClick={() => {
                        sendMessage({ text: p });
                      }}
                    >
                      {p}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {messages.map((m) => (
            <Message key={m.id} message={m} />
          ))}

          {error && (
            <div className="rounded-md border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-3 text-sm text-red-700 dark:text-red-300 font-mono">
              {error.message}
            </div>
          )}
        </div>
      </main>

      <footer className="border-t border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900">
        <form
          onSubmit={onSubmit}
          className="max-w-3xl mx-auto px-4 py-3 flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about Colorado cancer data…"
            className="flex-1 rounded-md border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={busy}
            autoFocus
          />
          {busy ? (
            <button
              type="button"
              onClick={() => stop()}
              className="rounded-md bg-slate-800 dark:bg-slate-200 text-white dark:text-slate-900 px-4 py-2 text-sm font-medium"
            >
              Stop
            </button>
          ) : (
            <button
              type="submit"
              className="rounded-md bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 text-sm font-medium disabled:opacity-50"
              disabled={!input.trim()}
            >
              Send
            </button>
          )}
        </form>
      </footer>
    </div>
  );
}

type UIMessageLike = {
  id: string;
  role: "user" | "assistant" | "system";
  parts: Array<{
    type: string;
    text?: string;
    toolName?: string;
    output?: unknown;
    state?: string;
  }>;
};

function Message({ message }: { message: UIMessageLike }) {
  const isUser = message.role === "user";
  return (
    <div className={isUser ? "flex justify-end" : ""}>
      <div
        className={
          isUser
            ? "max-w-[80%] rounded-2xl bg-blue-600 text-white px-4 py-2 text-sm"
            : "max-w-full w-full text-sm text-slate-800 dark:text-slate-100"
        }
      >
        {message.parts.map((part, i) => {
          if (part.type === "text") {
            if (isUser) {
              return (
                <span key={i} className="whitespace-pre-wrap">
                  {part.text}
                </span>
              );
            }
            return (
              <div key={i} className="prose prose-slate dark:prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {part.text ?? ""}
                </ReactMarkdown>
              </div>
            );
          }
          if (part.type.startsWith("tool-")) {
            const toolName = part.type.slice("tool-".length);
            if (part.state === "output-available") {
              return (
                <ToolResult key={i} toolName={toolName} output={part.output} />
              );
            }
            return (
              <div
                key={i}
                className="text-xs text-slate-500 dark:text-slate-400 italic my-1"
              >
                calling {toolName}…
              </div>
            );
          }
          return null;
        })}
      </div>
    </div>
  );
}

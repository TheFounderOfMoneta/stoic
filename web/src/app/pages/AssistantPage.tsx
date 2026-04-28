import { useEffect, useRef, useState } from "react";
import { apiClient } from "@/app/apiClient";
import stoicLogo from "@/assets/stoic-logo.png";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  Copy,
  FileText,
  Gauge,
  Image as ImageIcon,
  MessageSquarePlus,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Shield,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

type Message = {
  id: string;
  from: "me" | "ai" | "system";
  text: string;
  time: string;
};

type Chat = {
  id: string;
  title: string;
  conversationId: string | null;
  messages: Message[];
  updatedAt: string;
};

type CodexStatus = {
  available: boolean;
  version: string | null;
  auth_present: boolean;
  auth_path: string;
  workdir: string;
  default_model: string;
  models: string[];
  sandbox_modes: SandboxMode[];
  reasoning_efforts: ReasoningEffort[];
  default_reasoning_effort: ReasoningEffort;
  error: string | null;
};

type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
type ReasoningEffort = "low" | "medium" | "high" | "xhigh";

type AttachmentPayload = {
  name: string;
  mime_type: string;
  data_base64?: string;
  text?: string;
};

type PendingAttachment = AttachmentPayload & {
  id: string;
  size: number;
  kind: "image" | "text" | "file";
};

const INTRO: Message = {
  id: "intro",
  from: "ai",
  text:
    "Привет. Я подключусь к локальному Codex CLI на этой машине и буду использовать его auth.json. Выбери модель и уровень прав, затем отправь задачу.",
  time: "10:00",
};

const SUGGESTIONS = [
  "Проверь архитектуру проекта и найди слабые места",
  "Помоги реализовать следующую задачу в этом репозитории",
  "Объясни, как устроен backend Stoic",
  "Найди, где в проекте подключается роутинг",
];

const SANDBOX_LABELS: Record<SandboxMode, string> = {
  "read-only": "Только чтение",
  "workspace-write": "Рабочая папка",
  "danger-full-access": "Полный доступ",
};

const SANDBOX_OPTIONS: SandboxMode[] = ["read-only", "workspace-write", "danger-full-access"];

const EFFORT_LABELS: Record<ReasoningEffort, string> = {
  low: "Низкая",
  medium: "Средняя",
  high: "Высокая",
  xhigh: "Макс",
};

const EFFORT_OPTIONS: ReasoningEffort[] = ["low", "medium", "high", "xhigh"];

function timeNow() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function newChat(title = "Новый чат"): Chat {
  return {
    id: crypto.randomUUID(),
    title,
    conversationId: null,
    messages: [INTRO],
    updatedAt: "Сейчас",
  };
}

async function readError(response: Response) {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    return JSON.stringify(body.detail ?? body);
  } catch {
    return response.statusText || "Ошибка запроса";
  }
}

function isTextFile(file: File) {
  return (
    file.type.startsWith("text/") ||
    /\.(md|txt|json|csv|ts|tsx|js|jsx|py|toml|yaml|yml|css|html)$/i.test(file.name)
  );
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",")[1] : result);
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function AssistantPage() {
  const initialChatRef = useRef<Chat | null>(null);
  if (!initialChatRef.current) initialChatRef.current = newChat();

  const [chats, setChats] = useState<Chat[]>([initialChatRef.current]);
  const [activeChatId, setActiveChatId] = useState(initialChatRef.current.id);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [model, setModel] = useState("gpt-5.2");
  const [reasoningEffort, setReasoningEffort] = useState<ReasoningEffort>("medium");
  const [sandbox, setSandbox] = useState<SandboxMode>("read-only");
  const [openMenu, setOpenMenu] = useState<"model" | "sandbox" | "effort" | null>(null);
  const [contextMenu, setContextMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const activeChat = chats.find((chat) => chat.id === activeChatId) ?? chats[0];
  const models = status?.models.length ? status.models : ["gpt-5.2"];
  const reasoningEfforts = status?.reasoning_efforts?.length ? status.reasoning_efforts : EFFORT_OPTIONS;

  useEffect(() => {
    let cancelled = false;

    async function loadStatus() {
      try {
        const response = await fetch("/api/v1/codex/status");
        if (!response.ok) throw new Error(await readError(response));
        const body = (await response.json()) as CodexStatus;
        if (cancelled) return;
        setStatus(body);
        setModel(body.default_model || body.models[0] || "gpt-5.2");
        setReasoningEffort(body.default_reasoning_effort || "medium");
      } catch (error) {
        if (!cancelled) {
          setStatus({
            available: false,
            version: null,
            auth_present: false,
            auth_path: "~/.codex/auth.json",
            workdir: "",
            default_model: "gpt-5.2",
            models: ["gpt-5.2"],
            sandbox_modes: SANDBOX_OPTIONS,
            reasoning_efforts: EFFORT_OPTIONS,
            default_reasoning_effort: "medium",
            error: error instanceof Error ? error.message : "Не удалось проверить Codex",
          });
        }
      }
    }

    loadStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const close = () => {
      setOpenMenu(null);
      setContextMenu(null);
    };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeChat.messages, loading]);

  const updateActiveChat = (updater: (chat: Chat) => Chat) => {
    setChats((prev) => prev.map((chat) => (chat.id === activeChat.id ? updater(chat) : chat)));
  };

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    const selected = Array.from(files).slice(0, 6);
    const parsed = await Promise.all(
      selected.map(async (file) => {
        const base = {
          id: crypto.randomUUID(),
          name: file.name,
          mime_type: file.type || "application/octet-stream",
          size: file.size,
        };

        if (file.type.startsWith("image/")) {
          return { ...base, kind: "image" as const, data_base64: await fileToBase64(file) };
        }

        if (isTextFile(file)) {
          return { ...base, kind: "text" as const, text: await file.text() };
        }

        return { ...base, kind: "file" as const, data_base64: await fileToBase64(file) };
      }),
    );
    setAttachments((prev) => [...prev, ...parsed]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const send = async (text?: string) => {
    const typedMessage = (text ?? input).trim();
    const message = typedMessage || (attachments.length ? "Проанализируй вложения." : "");
    if (!message || loading) return;

    const outgoingAttachments = attachments;
    setInput("");
    setAttachments([]);
    setLoading(true);

    const userText =
      outgoingAttachments.length > 0
        ? `${message}\n\nВложения: ${outgoingAttachments.map((file) => file.name).join(", ")}`
        : message;

    updateActiveChat((chat) => ({
      ...chat,
      title: chat.title === "Новый чат" ? message.slice(0, 42) : chat.title,
      messages: [...chat.messages, { id: crypto.randomUUID(), from: "me", text: userText, time: timeNow() }],
      updatedAt: "Сейчас",
    }));

    try {
      const response = await apiClient("/api/v1/messages/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          conversation_id: activeChat.conversationId,
          model,
          reasoning_effort: reasoningEffort,
          sandbox,
          approval_policy: "on-request",
          attachments: outgoingAttachments.map(({ id, size, kind, ...attachment }) => attachment),
        }),
      });

      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as { conversation_id: string; message: string };

      updateActiveChat((chat) => ({
        ...chat,
        conversationId: body.conversation_id,
        messages: [...chat.messages, { id: crypto.randomUUID(), from: "ai", text: body.message, time: timeNow() }],
        updatedAt: "Сейчас",
      }));
    } catch (error) {
      updateActiveChat((chat) => ({
        ...chat,
        messages: [
          ...chat.messages,
          {
            id: crypto.randomUUID(),
            from: "system",
            text: error instanceof Error ? error.message : "Codex не ответил",
            time: timeNow(),
          },
        ],
      }));
    } finally {
      setLoading(false);
    }
  };

  const createChat = () => {
    const chat = newChat();
    setChats((prev) => [chat, ...prev]);
    setActiveChatId(chat.id);
    setInput("");
    setAttachments([]);
  };

  const renameChat = (chatId: string) => {
    const chat = chats.find((item) => item.id === chatId);
    if (!chat) return;
    const title = window.prompt("Новое имя чата", chat.title)?.trim();
    if (!title) return;
    setChats((prev) => prev.map((item) => (item.id === chatId ? { ...item, title } : item)));
  };

  const deleteChat = (chatId: string) => {
    setChats((prev) => {
      const next = prev.filter((chat) => chat.id !== chatId);
      if (next.length) {
        if (activeChatId === chatId) setActiveChatId(next[0].id);
        return next;
      }
      const replacement = newChat();
      setActiveChatId(replacement.id);
      return [replacement];
    });
  };

  const canSend =
    (input.trim().length > 0 || attachments.length > 0) && !loading && Boolean(status?.available && status.auth_present);
  const showSuggestions = activeChat.messages.length === 1 && !loading;

  return (
    <div className="flex h-full overflow-hidden">
      <aside className="hidden w-[260px] flex-shrink-0 flex-col border-r border-gray-100 bg-white md:flex">
        <div className="border-b border-gray-100 p-4">
          <button
            onClick={createChat}
            className="flex w-full items-center gap-2 rounded-xl bg-[#EEF3FE] px-3 py-2 text-[13px] font-medium text-[#4B78F5] transition-colors hover:bg-blue-100"
          >
            <MessageSquarePlus size={15} />
            Новый чат
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-3">
          {chats.map((chat) => (
            <button
              key={chat.id}
              onClick={() => setActiveChatId(chat.id)}
              onContextMenu={(event) => {
                event.preventDefault();
                setContextMenu({ chatId: chat.id, x: event.clientX, y: event.clientY });
              }}
              className={`mb-1 w-full rounded-xl px-3 py-2.5 text-left transition-colors ${
                chat.id === activeChat.id ? "bg-[#EEF3FE]" : "hover:bg-gray-50"
              }`}
            >
              <p className={`truncate text-[12.5px] ${chat.id === activeChat.id ? "font-medium text-[#4B78F5]" : "text-gray-700"}`}>
                {chat.title}
              </p>
              <p className="mt-0.5 text-[10.5px] text-gray-400">{chat.updatedAt}</p>
            </button>
          ))}
        </div>

        {contextMenu && (
          <div
            className="fixed z-[10000] w-44 rounded-2xl border border-gray-100 bg-white p-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.13),0_2px_8px_rgba(0,0,0,0.07)]"
            style={{ top: contextMenu.y, left: contextMenu.x }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              onClick={() => {
                renameChat(contextMenu.chatId);
                setContextMenu(null);
              }}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-[12.5px] text-gray-600 hover:bg-gray-50"
            >
              <Pencil size={13} />
              Переименовать
            </button>
            <button
              onClick={() => {
                deleteChat(contextMenu.chatId);
                setContextMenu(null);
              }}
              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-[12.5px] text-red-500 hover:bg-red-50"
            >
              <Trash2 size={13} />
              Удалить
            </button>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col bg-[#F6F7FB]">
        <header className="flex items-center gap-3 border-b border-gray-100 bg-white px-5 py-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-[#EEF3FE]">
            <img src={stoicLogo} alt="Stoic" className="h-5 w-5 object-contain" />
          </div>
          <p className="text-[13.5px] font-semibold text-gray-800">Stoic</p>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
          <div className="mx-auto flex max-w-5xl flex-col gap-6">
            {activeChat.messages.map((msg) => (
              <div key={msg.id} className={`flex gap-3 ${msg.from === "me" ? "justify-end" : "justify-start"}`}>
                {msg.from !== "me" && (
                  <div
                    className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl ${
                      msg.from === "system" ? "bg-amber-100" : "bg-[#EEF3FE]"
                    }`}
                  >
                    {msg.from === "system" ? (
                      <AlertTriangle size={14} className="text-amber-600" />
                    ) : (
                      <img src={stoicLogo} alt="Stoic" className="h-5 w-5 object-contain" />
                    )}
                  </div>
                )}
                <div className="max-w-[88%] sm:max-w-[72%]">
                  <div
                    className="whitespace-pre-wrap rounded-2xl px-4 py-3 text-[13px] leading-relaxed"
                    style={
                      msg.from === "me"
                        ? { background: "#4B78F5", color: "white", borderBottomRightRadius: 6 }
                        : msg.from === "system"
                          ? { background: "#FFF7E6", color: "#8A5B00", borderBottomLeftRadius: 6 }
                          : {
                              background: "white",
                              color: "#374151",
                              borderBottomLeftRadius: 6,
                              boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
                            }
                    }
                  >
                    {msg.text}
                  </div>
                  <div className={`mt-1.5 flex items-center gap-2 ${msg.from === "me" ? "justify-end" : "justify-start"}`}>
                    <span className="text-[10.5px] text-gray-400">{msg.time}</span>
                    {msg.from === "ai" && (
                      <button
                        onClick={() => navigator.clipboard?.writeText(msg.text)}
                        className="flex h-6 w-6 items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-200 hover:text-gray-600"
                      >
                        <Copy size={11} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start gap-3">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-[#4B78F5] to-[#6C5CE7]">
                  <Sparkles size={14} className="text-white" />
                </div>
                <div className="rounded-2xl bg-white px-4 py-3 shadow-[0_1px_4px_rgba(0,0,0,0.06)]">
                  <div className="flex h-5 items-center gap-1.5">
                    {[0, 1, 2].map((item) => (
                      <div
                        key={item}
                        className="h-2 w-2 rounded-full bg-[#4B78F5]"
                        style={{ animation: `bounce 1.2s ${item * 0.2}s infinite` }}
                      />
                    ))}
                  </div>
                </div>
              </div>
            )}

            {showSuggestions && (
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="rounded-xl border border-gray-100 bg-white px-4 py-3 text-left text-[12.5px] text-gray-700 shadow-[0_1px_3px_rgba(0,0,0,0.05)] transition-all hover:border-blue-200 hover:bg-[#EEF3FE] hover:text-[#4B78F5]"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        <div className="px-4 pb-5 pt-3 sm:px-8">
          <div className="relative mx-auto max-w-5xl rounded-2xl border border-gray-200 bg-white shadow-[0_2px_8px_rgba(0,0,0,0.06)]">
            {attachments.length > 0 && (
              <div className="flex flex-wrap gap-2 border-b border-gray-100 px-3 py-2">
                {attachments.map((file) => (
                  <div key={file.id} className="flex max-w-[220px] items-center gap-2 rounded-xl bg-[#F6F8FC] px-2.5 py-1.5 text-[11.5px] text-gray-600">
                    {file.kind === "image" ? <ImageIcon size={13} /> : <FileText size={13} />}
                    <span className="truncate">{file.name}</span>
                    <button
                      onClick={() => setAttachments((prev) => prev.filter((item) => item.id !== file.id))}
                      className="text-gray-400 hover:text-gray-700"
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  send();
                }
              }}
              placeholder={status?.auth_present === false ? "Codex auth.json не найден на сервере..." : "Напиши сообщение для Stoic..."}
              rows={2}
              className="w-full resize-none rounded-t-2xl px-4 pb-1 pt-3 text-[13px] leading-relaxed text-gray-700 outline-none placeholder:text-gray-400"
            />

            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(event) => handleFiles(event.target.files)}
            />

            <div className="flex flex-wrap items-center justify-between gap-2 px-3 pb-2.5">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <button
                  onClick={() => fileInputRef.current?.click()}
                  className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-700"
                >
                  <Plus size={17} />
                </button>

                <div className="relative">
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpenMenu(openMenu === "sandbox" ? null : "sandbox");
                    }}
                    className={`flex h-8 items-center gap-1.5 rounded-xl px-2.5 text-[12.5px] font-semibold transition-colors ${
                      sandbox === "danger-full-access"
                        ? "bg-orange-50 text-orange-600"
                        : "bg-[#F6F8FC] text-[#4F628C] hover:bg-[#EEF3FE] hover:text-[#2F73FF]"
                    }`}
                  >
                    <Shield size={13} />
                    {SANDBOX_LABELS[sandbox]}
                    <ChevronDown size={13} />
                  </button>

                  {openMenu === "sandbox" && (
                    <div
                      className="absolute bottom-[calc(100%+8px)] left-0 z-50 w-48 rounded-2xl border border-gray-100 bg-white p-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.13),0_2px_8px_rgba(0,0,0,0.07)]"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {SANDBOX_OPTIONS.map((item) => (
                        <button
                          key={item}
                          onClick={() => {
                            setSandbox(item);
                            setOpenMenu(null);
                          }}
                          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12.5px] ${
                            sandbox === item ? "bg-[#EEF3FE] text-[#2F73FF]" : "text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          <Shield size={13} />
                          {SANDBOX_LABELS[item]}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2">
                {loading && <RefreshCw size={14} className="animate-spin text-gray-400" />}
                <div className="relative">
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpenMenu(openMenu === "effort" ? null : "effort");
                    }}
                    className="flex h-8 items-center gap-1.5 rounded-xl bg-[#F6F8FC] px-2.5 text-[12.5px] font-semibold text-[#253657] transition-colors hover:bg-[#EEF3FE] hover:text-[#2F73FF]"
                  >
                    <Gauge size={13} />
                    {EFFORT_LABELS[reasoningEffort]}
                    <ChevronDown size={13} />
                  </button>

                  {openMenu === "effort" && (
                    <div
                      className="absolute bottom-[calc(100%+8px)] right-0 z-50 w-44 rounded-2xl border border-gray-100 bg-white p-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.13),0_2px_8px_rgba(0,0,0,0.07)]"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {reasoningEfforts.map((item) => (
                        <button
                          key={item}
                          onClick={() => {
                            setReasoningEffort(item);
                            setOpenMenu(null);
                          }}
                          className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-[12.5px] ${
                            reasoningEffort === item ? "bg-[#EEF3FE] font-semibold text-[#2F73FF]" : "text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          <Gauge size={13} />
                          {EFFORT_LABELS[item]}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="relative">
                  <button
                    onClick={(event) => {
                      event.stopPropagation();
                      setOpenMenu(openMenu === "model" ? null : "model");
                    }}
                    className="flex h-8 items-center gap-1.5 rounded-xl bg-[#F6F8FC] px-2.5 text-[12.5px] font-semibold text-[#253657] transition-colors hover:bg-[#EEF3FE] hover:text-[#2F73FF]"
                  >
                    {model}
                    <ChevronDown size={13} />
                  </button>

                  {openMenu === "model" && (
                    <div
                      className="absolute bottom-[calc(100%+8px)] right-0 z-50 w-48 rounded-2xl border border-gray-100 bg-white p-1.5 shadow-[0_12px_40px_rgba(0,0,0,0.13),0_2px_8px_rgba(0,0,0,0.07)]"
                      onClick={(event) => event.stopPropagation()}
                    >
                      {models.map((item) => (
                        <button
                          key={item}
                          onClick={() => {
                            setModel(item);
                            setOpenMenu(null);
                          }}
                          className={`w-full rounded-xl px-3 py-2 text-left text-[12.5px] ${
                            model === item ? "bg-[#EEF3FE] font-semibold text-[#2F73FF]" : "text-gray-600 hover:bg-gray-50"
                          }`}
                        >
                          {item}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <button
                  onClick={() => send()}
                  disabled={!canSend}
                  className="flex items-center gap-2 rounded-xl px-3.5 py-1.5 text-[12.5px] font-medium text-white transition-all disabled:opacity-40"
                  style={{ background: "linear-gradient(135deg, #4B78F5, #6C5CE7)" }}
                >
                  <Send size={13} />
                  Отправить
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <style>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-5px); }
        }
      `}</style>
    </div>
  );
}

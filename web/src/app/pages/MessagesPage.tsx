import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { apiClient } from "@/app/apiClient";
import {
  LoaderCircle,
  MessageSquare,
  Pin,
  Search,
  Send,
  Smartphone,
} from "lucide-react";

type TelegramAuthStatusResponse = {
  authorized: boolean;
  user: {
    id: number;
    username: string | null;
    phone: string | null;
    first_name: string | null;
    last_name: string | null;
  } | null;
};

type TelegramDialog = {
  id: number;
  title: string;
  entity: string;
  entity_type: string;
  kind: "contact" | "group";
  username: string | null;
  unread_count: number;
  message_count_hint: number | null;
  conversation_id: string;
  app_pinned: boolean;
  is_pinned: boolean;
  telegram_pinned: boolean;
  position: number | null;
  last_message_at: string | null;
  last_message_text: string | null;
};

type TelegramDialogListResponse = {
  items: TelegramDialog[];
  limit: number;
};

type TelegramMessage = {
  id: string;
  channel: "telegram";
  sender_role: "user" | "contact" | "system" | "assistant";
  text: string;
  captured_at: string;
  sender_name: string | null;
};

type MessageListResponse = {
  items: TelegramMessage[];
  limit: number;
  offset: number;
};

function initials(title: string) {
  const words = title.trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "").join("") || "TG";
}

function badgeColor(seed: string) {
  const colors = ["#4B78F5", "#00B894", "#6C5CE7", "#FD9644", "#0984E3", "#E17055", "#A29BFE"];
  let sum = 0;
  for (const char of seed) sum += char.charCodeAt(0);
  return colors[sum % colors.length];
}

function formatMessageTime(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function sortDialogs(items: TelegramDialog[]) {
  return [...items].sort((left, right) => {
    if (left.app_pinned !== right.app_pinned) return left.app_pinned ? -1 : 1;
    if (left.telegram_pinned !== right.telegram_pinned) return left.telegram_pinned ? -1 : 1;
    const leftPosition = left.position ?? Number.MAX_SAFE_INTEGER;
    const rightPosition = right.position ?? Number.MAX_SAFE_INTEGER;
    if (leftPosition !== rightPosition) return leftPosition - rightPosition;
    const leftTime = left.last_message_at ? new Date(left.last_message_at).getTime() : -Infinity;
    const rightTime = right.last_message_at ? new Date(right.last_message_at).getTime() : -Infinity;
    if (leftTime !== rightTime) return rightTime - leftTime;
    return left.title.localeCompare(right.title, "ru");
  });
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

export function MessagesPage() {
  const [authStatus, setAuthStatus] = useState<TelegramAuthStatusResponse | null>(null);
  const [dialogs, setDialogs] = useState<TelegramDialog[]>([]);
  const [selectedDialogId, setSelectedDialogId] = useState<number | null>(null);
  const [messages, setMessages] = useState<TelegramMessage[]>([]);
  const [search, setSearch] = useState("");
  const [input, setInput] = useState("");
  const [dialogsLoading, setDialogsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [sendLoading, setSendLoading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [lastDialogsSyncAt, setLastDialogsSyncAt] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const selectedDialog = dialogs.find((dialog) => dialog.id === selectedDialogId) ?? null;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sendLoading]);

  const loadDialogs = async () => {
    try {
      setDialogsLoading(true);
      setPageError(null);

      const authResponse = await apiClient("/api/v1/messages/telegram/auth/status");
      if (!authResponse.ok) throw new Error(await readError(authResponse));
      const authBody = (await authResponse.json()) as TelegramAuthStatusResponse;
      setAuthStatus(authBody);

      if (!authBody.authorized) {
        setDialogs([]);
        setSelectedDialogId(null);
        return;
      }

      const dialogsResponse = await apiClient("/api/v1/messages/telegram/dialogs?limit=300");
      if (!dialogsResponse.ok) throw new Error(await readError(dialogsResponse));
      const dialogsBody = (await dialogsResponse.json()) as TelegramDialogListResponse;
      const sortedDialogs = sortDialogs(dialogsBody.items);
      setDialogs(sortedDialogs);
      setSelectedDialogId((current) => current ?? sortedDialogs[0]?.id ?? null);
      setLastDialogsSyncAt(new Date().toISOString());
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Не удалось загрузить диалоги.");
    } finally {
      setDialogsLoading(false);
    }
  };

  const loadMessages = async (dialog: TelegramDialog) => {
    try {
      setMessagesLoading(true);
      setThreadError(null);
      const response = await apiClient(
        `/api/v1/messages/conversations/${encodeURIComponent(dialog.conversation_id)}/messages?limit=100`,
      );
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as MessageListResponse;
      setMessages(body.items);
    } catch (error) {
      setThreadError(error instanceof Error ? error.message : "Не удалось загрузить сообщения.");
    } finally {
      setMessagesLoading(false);
    }
  };

  useEffect(() => {
    void loadDialogs();
  }, []);

  useEffect(() => {
    if (!selectedDialog) {
      setMessages([]);
      return;
    }
    void loadMessages(selectedDialog);
  }, [selectedDialogId]);

  useEffect(() => {
    if (!authStatus?.authorized) return;
    const intervalId = window.setInterval(() => {
      void pollDialogDeltas();
      void pollMessageDeltas();
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, [authStatus?.authorized, selectedDialog?.conversation_id, lastDialogsSyncAt, messages]);

  const pollDialogDeltas = async () => {
    if (!authStatus?.authorized || !lastDialogsSyncAt) return;
    try {
      const response = await apiClient(
        `/api/v1/messages/telegram/dialogs?limit=300&changed_after=${encodeURIComponent(lastDialogsSyncAt)}`,
      );
      if (!response.ok) return;
      const body = (await response.json()) as TelegramDialogListResponse;
      if (!body.items.length) return;
      setDialogs((prev) => {
        const map = new Map(prev.map((item) => [item.id, item]));
        for (const item of body.items) map.set(item.id, item);
        return sortDialogs(Array.from(map.values()));
      });
      setLastDialogsSyncAt(new Date().toISOString());
    } catch {
      return;
    }
  };

  const pollMessageDeltas = async () => {
    if (!selectedDialog) return;
    const lastMessage = messages[messages.length - 1];
    const afterQuery = lastMessage ? `&after=${encodeURIComponent(lastMessage.captured_at)}` : "";
    try {
      const response = await apiClient(
        `/api/v1/messages/conversations/${encodeURIComponent(selectedDialog.conversation_id)}/messages?limit=100${afterQuery}`,
      );
      if (!response.ok) return;
      const body = (await response.json()) as MessageListResponse;
      if (!body.items.length) return;
      setMessages((prev) => [...prev, ...body.items.filter((item) => !prev.some((existing) => existing.id === item.id))]);
    } catch {
      return;
    }
  };

  const filteredDialogs = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return dialogs;
    return dialogs.filter((dialog) => {
      const title = dialog.title.toLowerCase();
      const username = dialog.username?.toLowerCase() ?? "";
      return title.includes(query) || username.includes(query);
    });
  }, [dialogs, search]);

  const togglePin = async (dialog: TelegramDialog) => {
    try {
      const response = await apiClient(`/api/v1/messages/telegram/dialogs/${dialog.id}/pin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: !dialog.app_pinned }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const updatedDialog = (await response.json()) as TelegramDialog;
      setDialogs((prev) => {
        const map = new Map(prev.map((item) => [item.id, item]));
        map.set(updatedDialog.id, updatedDialog);
        return sortDialogs(Array.from(map.values()));
      });
      setLastDialogsSyncAt(new Date().toISOString());
      setPageError(null);
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "Не удалось изменить закрепление.");
    }
  };

  const sendMessage = async () => {
    if (!selectedDialog || !input.trim() || sendLoading) return;

    const text = input.trim();
    setInput("");
    setSendLoading(true);
    setThreadError(null);

    try {
      const response = await apiClient(
        `/api/v1/messages/telegram/dialogs/${encodeURIComponent(selectedDialog.entity)}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        },
      );
      if (!response.ok) throw new Error(await readError(response));
      const sentMessage = (await response.json()) as TelegramMessage;
      setMessages((prev) => [...prev, sentMessage]);
      await pollDialogDeltas();
    } catch (error) {
      setThreadError(error instanceof Error ? error.message : "Не удалось отправить сообщение.");
      setInput(text);
    } finally {
      setSendLoading(false);
    }
  };

  return (
    <div className="flex h-full overflow-hidden bg-white">
      <aside className="flex w-[320px] shrink-0 flex-col border-r border-gray-100 bg-white">
        <div className="border-b border-gray-100 px-4 py-4">
          <div className="mb-3 flex items-center gap-2">
            <h2 className="flex-1 text-[15px] font-semibold text-gray-800">Сообщения</h2>
            <button
              onClick={() => void loadDialogs()}
              className="rounded-xl bg-[#EEF3FE] px-3 py-1.5 text-[11.5px] font-medium text-[#4B78F5] transition-colors hover:bg-blue-100"
            >
              Обновить
            </button>
          </div>

          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Поиск по контактам и группам..."
              className="w-full rounded-xl border border-gray-100 bg-gray-50 py-2 pl-9 pr-3 text-[12.5px] text-gray-700 outline-none focus:border-blue-200"
            />
          </div>
        </div>

        {!authStatus?.authorized && !dialogsLoading ? (
          <div className="m-4 rounded-2xl border border-amber-200 bg-amber-50 p-4">
            <p className="text-[13px] font-semibold text-amber-800">Telegram не подключен</p>
            <p className="mt-1 text-[12px] text-amber-700">
              Подключите Telegram в настройках, чтобы читать и отправлять сообщения.
            </p>
            <Link
              to="/settings?tab=integrations"
              className="mt-3 inline-flex rounded-xl bg-white px-3 py-2 text-[12px] font-medium text-amber-700 transition-colors hover:bg-amber-100"
            >
              Открыть настройки
            </Link>
          </div>
        ) : null}

        {pageError ? (
          <div className="m-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-600">{pageError}</div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto">
          {dialogsLoading ? (
            <div className="flex h-full items-center justify-center text-[12.5px] text-gray-400">
              <LoaderCircle size={16} className="mr-2 animate-spin" />
              Загружаем диалоги...
            </div>
          ) : filteredDialogs.length === 0 ? (
            <div className="px-4 py-6 text-[12.5px] text-gray-400">
              {authStatus?.authorized ? "Контактов и групп пока нет." : "Нет доступных диалогов."}
            </div>
          ) : (
            filteredDialogs.map((dialog) => {
              const color = badgeColor(dialog.title);
              const isSelected = dialog.id === selectedDialogId;
              return (
                <div
                  key={dialog.id}
                  onClick={() => setSelectedDialogId(dialog.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedDialogId(dialog.id);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  className={`flex w-full items-center gap-3 border-b border-gray-50 px-4 py-3 text-left transition-colors ${
                    isSelected ? "bg-[#EEF3FE]" : "hover:bg-gray-50"
                  }`}
                >
                  <div
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
                    style={{ background: color }}
                  >
                    {initials(dialog.title)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-semibold text-gray-800">{dialog.title}</span>
                      {dialog.is_pinned ? <Pin size={12} className="shrink-0 text-[#4B78F5]" /> : null}
                      <span className="rounded-full bg-[#F3F6FB] px-2 py-0.5 text-[10px] font-medium text-[#7182A8]">
                        {dialog.kind === "group" ? "Группа" : "Контакт"}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-[11px] text-gray-400">
                      {dialog.last_message_text?.trim()
                        ? dialog.last_message_text
                        : dialog.unread_count > 0
                          ? `Непрочитано: ${dialog.unread_count}`
                          : "Без новых непрочитанных"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      void togglePin(dialog);
                    }}
                    className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl transition-colors ${
                      dialog.app_pinned ? "bg-[#EEF3FE] text-[#4B78F5]" : "text-gray-300 hover:bg-gray-100 hover:text-gray-500"
                    }`}
                    title={dialog.app_pinned ? "Убрать закрепление в Stoic" : "Закрепить в Stoic"}
                  >
                    <Pin size={14} />
                  </button>
                </div>
              );
            })
          )}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col bg-[#F8FAFE]">
        {selectedDialog ? (
          <>
            <div className="flex items-center gap-3 border-b border-gray-100 bg-white px-5 py-3">
              <div
                className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-[11px] font-bold text-white"
                style={{ background: badgeColor(selectedDialog.title) }}
              >
                {initials(selectedDialog.title)}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[14px] font-semibold text-gray-800">{selectedDialog.title}</p>
                <p className="text-[11px] text-gray-400">
                  Telegram · {selectedDialog.kind === "group" ? "группа" : "контакт"}
                </p>
              </div>
              <button
                onClick={() => void togglePin(selectedDialog)}
                className={`grid h-9 w-9 place-items-center rounded-xl transition-colors ${
                  selectedDialog.app_pinned ? "bg-[#EEF3FE] text-[#4B78F5]" : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                }`}
                title={selectedDialog.app_pinned ? "Убрать закрепление в Stoic" : "Закрепить в Stoic"}
              >
                <Pin size={14} />
              </button>
              <div className="inline-flex items-center gap-1 rounded-full bg-[#EEF3FE] px-3 py-1 text-[11px] font-semibold text-[#4B78F5]">
                <Smartphone size={12} />
                live
              </div>
            </div>

            {threadError ? (
              <div className="mx-5 mt-4 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-[12px] text-red-600">
                {threadError}
              </div>
            ) : null}

            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
              {messagesLoading ? (
                <div className="flex h-full items-center justify-center text-[12.5px] text-gray-400">
                  <LoaderCircle size={16} className="mr-2 animate-spin" />
                  Загружаем сообщения...
                </div>
              ) : messages.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center text-center">
                  <div
                    className="mb-3 grid h-16 w-16 place-items-center rounded-2xl"
                    style={{ background: `${badgeColor(selectedDialog.title)}18` }}
                  >
                    <MessageSquare size={28} style={{ color: badgeColor(selectedDialog.title) }} />
                  </div>
                  <p className="text-[14px] font-medium text-gray-700">{selectedDialog.title}</p>
                  <p className="mt-1 max-w-[460px] text-[12px] text-gray-400">
                    Здесь показываются только новые сообщения после подключения Telegram. Старая история намеренно не загружается.
                  </p>
                </div>
              ) : (
                <div className="space-y-4">
                  {messages.map((message) => (
                    <div
                      key={message.id}
                      className={`flex ${message.sender_role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div className="max-w-[72%]">
                        <div
                          className="rounded-2xl px-4 py-2.5 text-[13px] leading-relaxed"
                          style={
                            message.sender_role === "user"
                              ? { background: "#4B78F5", color: "white", borderBottomRightRadius: 6 }
                              : {
                                  background: "white",
                                  color: "#374151",
                                  borderBottomLeftRadius: 6,
                                  boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
                                }
                          }
                        >
                          {message.text}
                        </div>
                        <div
                          className={`mt-1 text-[10.5px] text-gray-400 ${
                            message.sender_role === "user" ? "text-right" : "text-left"
                          }`}
                        >
                          {formatMessageTime(message.captured_at)}
                        </div>
                      </div>
                    </div>
                  ))}
                  <div ref={bottomRef} />
                </div>
              )}
            </div>

            <div className="border-t border-gray-100 bg-white px-5 py-4">
              <div className="flex items-end gap-3 rounded-2xl border border-gray-100 bg-[#F8FAFE] px-4 py-3">
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  rows={1}
                  placeholder="Написать сообщение в Telegram..."
                  className="min-h-[24px] flex-1 resize-none bg-transparent text-[13px] text-gray-700 outline-none placeholder:text-gray-400"
                  style={{ maxHeight: 120 }}
                />
                <button
                  onClick={() => void sendMessage()}
                  disabled={!input.trim() || sendLoading}
                  className="grid h-9 w-9 place-items-center rounded-xl bg-[linear-gradient(135deg,#4B78F5,#6C5CE7)] text-white transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {sendLoading ? <LoaderCircle size={15} className="animate-spin" /> : <Send size={15} />}
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="flex h-full items-center justify-center text-center">
            <div>
              <MessageSquare size={28} className="mx-auto text-gray-300" />
              <p className="mt-3 text-[14px] font-medium text-gray-600">Выберите контакт или группу</p>
              <p className="mt-1 text-[12px] text-gray-400">Слева показываются реальные Telegram-диалоги.</p>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { apiClient } from "@/app/apiClient";
import stoicLogo from "@/assets/stoic-logo.png";
import {
  Activity,
  ArrowRight,
  Bot,
  Briefcase,
  Calendar,
  Check,
  CheckSquare,
  FileText,
  FolderKanban,
  MoreVertical,
  NotebookPen,
  Plus,
  Send,
} from "lucide-react";

interface TaskDeadlineCountResponse {
  count: number;
}

interface TaskDeadlineItem {
  id: string;
  text: string;
  time: string;
  done: boolean;
}

interface TaskDeadlineListResponse {
  items: TaskDeadlineItem[];
}

interface TaskBacklogItem {
  id: string;
  text: string;
  updated_at: string;
}

interface TaskBacklogResponse {
  items: TaskBacklogItem[];
}

interface CalendarEvent {
  id: string;
  title: string;
  time: string;
  color: string;
  location?: string | null;
}

interface CalendarEventsResponse {
  items: CalendarEvent[];
}

interface EntitySummary {
  id: string;
  description?: string | null;
  primary_name?: string | null;
}

interface EntityListResponse {
  items: EntitySummary[];
}

interface IncomingSourceSummary {
  id: string;
  url_or_path?: string | null;
  captured_at: string;
  raw_text_preview?: string | null;
  metadata_json?: Record<string, unknown> | null;
}

interface IncomingSourceListResponse {
  items: IncomingSourceSummary[];
}

interface ChatMessage {
  id: string;
  from: "ai" | "me" | "system";
  text: string;
  time: string;
}

interface AssistantChatResponse {
  conversation_id: string;
  message: string;
}

interface StoredMessageItem {
  id: string;
  sender_role: "user" | "assistant" | "contact" | "system";
  text: string;
  captured_at: string;
}

interface MessageListResponse {
  items: StoredMessageItem[];
}

interface MessageConversationSummary {
  id: string;
}

interface MessageConversationListResponse {
  items: MessageConversationSummary[];
}

interface TelegramAuthStatusResponse {
  authorized: boolean;
}

interface TelegramDialogSummary {
  id: number;
  title: string;
  entity: string;
  kind: "contact" | "group";
  unread_count: number;
}

interface TelegramDialogListResponse {
  items: TelegramDialogSummary[];
}

interface DashboardState {
  todayTaskCount: number;
  todayTasks: TaskDeadlineItem[];
  backlogTasks: TaskBacklogItem[];
  schedule: CalendarEvent[];
  projects: EntitySummary[];
  notes: IncomingSourceSummary[];
  files: IncomingSourceSummary[];
  telegramAuthorized: boolean;
  telegramDialogs: TelegramDialogSummary[];
}

const initialDashboardState: DashboardState = {
  todayTaskCount: 0,
  todayTasks: [],
  backlogTasks: [],
  schedule: [],
  projects: [],
  notes: [],
  files: [],
  telegramAuthorized: false,
  telegramDialogs: [],
};

const introMessage: ChatMessage = {
  id: "home-intro",
  from: "ai",
  text: "Чем помочь сегодня?",
  time: "10:30",
};

const staticMessages = [
  {
    name: "Алексей Петров",
    initials: "АП",
    color: "#3B73F6",
    text: "Отличная идея! Согласен с...",
    time: "10:24",
    unread: 2,
  },
  {
    name: "Марина Иванова",
    initials: "МИ",
    color: "#13C6A3",
    text: "Спасибо! Жду материалы...",
    time: "09:48",
  },
  {
    name: "Игорь Смирнов",
    initials: "ИС",
    color: "#FF7A45",
    text: "Скину тебе файлы для рев...",
    time: "Вчера",
  },
  {
    name: "Команда маркетинга",
    initials: "КМ",
    color: "#6159F6",
    text: "Анна: Отличная работа!",
    time: "Вчера",
    unread: 1,
  },
];

function initials(title: string) {
  const words = title.trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 2).map((word) => word[0]?.toUpperCase() ?? "").join("") || "TG";
}

function badgeColor(seed: string) {
  const colors = ["#3B73F6", "#13C6A3", "#FF7A45", "#6159F6", "#0984E3", "#E17055", "#A29BFE"];
  let sum = 0;
  for (const char of seed) sum += char.charCodeAt(0);
  return colors[sum % colors.length];
}

function getBrowserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
}

function timeNow() {
  return new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function formatMessageTime(value: string) {
  return new Date(value).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function mapStoredMessage(message: StoredMessageItem): ChatMessage {
  return {
    id: message.id,
    from: message.sender_role === "user" ? "me" : message.sender_role === "assistant" ? "ai" : "system",
    text: message.text,
    time: formatMessageTime(message.captured_at),
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

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <section
      className={`rounded-[18px] border border-[#E7EBF4] bg-white shadow-[0_18px_45px_rgba(26,43,83,0.06)] ${className}`}
    >
      {children}
    </section>
  );
}

function ProgressRing({ value }: { value: number }) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (value / 100) * circumference;

  return (
    <div className="relative h-[90px] w-[90px] shrink-0">
      <svg viewBox="0 0 90 90" className="-rotate-90">
        <circle cx="45" cy="45" r={radius} stroke="#EEF2F8" strokeWidth="8" fill="none" />
        <circle
          cx="45"
          cy="45"
          r={radius}
          stroke="#17CDB8"
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center text-[16px] font-semibold text-[#7182A8]">
        {value}%
      </div>
    </div>
  );
}

function extractSourceTitle(source: IncomingSourceSummary) {
  const metadata = source.metadata_json;
  if (metadata && typeof metadata === "object") {
    for (const key of ["title", "name", "summary"]) {
      const value = metadata[key];
      if (typeof value === "string" && value.trim()) return value.trim();
    }
  }
  if (source.url_or_path) {
    const parts = source.url_or_path.split(/[\\/]/);
    const last = parts[parts.length - 1];
    if (last) return last;
  }
  return source.raw_text_preview?.trim() || source.id;
}

function EmptyTile({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-[#D9E3F2] bg-[#F8FAFE] px-4 py-4 text-left">
      <p className="text-[13px] font-semibold text-[#1A294A]">{title}</p>
      <p className="mt-1 text-[12px] leading-5 text-[#7D8FB3]">{description}</p>
    </div>
  );
}

async function loadJson<T>(path: string, signal: AbortSignal): Promise<T | null> {
  try {
    const response = await apiClient(path, { signal });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export function HomePage() {
  const navigate = useNavigate();
  const [dashboard, setDashboard] = useState<DashboardState>(initialDashboardState);
  const [now, setNow] = useState(() => new Date());
  const [assistantMessages, setAssistantMessages] = useState<ChatMessage[]>([introMessage]);
  const [assistantInput, setAssistantInput] = useState("");
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timezone = encodeURIComponent(getBrowserTimeZone());

    async function loadDashboardData() {
      const [
        taskListData,
        taskCountData,
        backlogData,
        calendarData,
        projectsData,
        notesData,
        filesData,
        telegramAuthData,
      ] = await Promise.all([
        loadJson<TaskDeadlineListResponse>(`/api/v1/tasks/deadlines/today?timezone=${timezone}`, controller.signal),
        loadJson<TaskDeadlineCountResponse>(`/api/v1/tasks/deadlines/today/count?timezone=${timezone}`, controller.signal),
        loadJson<TaskBacklogResponse>("/api/v1/tasks/backlog?limit=4", controller.signal),
        loadJson<CalendarEventsResponse>(`/api/v1/calendar/events/today?timezone=${timezone}`, controller.signal),
        loadJson<EntityListResponse>("/api/v1/entities?type_id=project&limit=4", controller.signal),
        loadJson<IncomingSourceListResponse>("/api/v1/incoming-sources?source_type=note&limit=4", controller.signal),
        loadJson<IncomingSourceListResponse>("/api/v1/incoming-sources?source_type=file&limit=4", controller.signal),
        loadJson<TelegramAuthStatusResponse>("/api/v1/messages/telegram/auth/status", controller.signal),
      ]);

      const telegramDialogs =
        telegramAuthData?.authorized
          ? await loadJson<TelegramDialogListResponse>("/api/v1/messages/telegram/dialogs?limit=4", controller.signal)
          : null;

      setDashboard({
        todayTaskCount: taskCountData?.count ?? taskListData?.items.length ?? 0,
        todayTasks: taskListData?.items ?? [],
        backlogTasks: backlogData?.items ?? [],
        schedule: calendarData?.items ?? [],
        projects: projectsData?.items ?? [],
        notes: notesData?.items ?? [],
        files: filesData?.items ?? [],
        telegramAuthorized: Boolean(telegramAuthData?.authorized),
        telegramDialogs: telegramDialogs?.items ?? [],
      });
    }

    loadDashboardData();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(intervalId);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    async function loadAssistantConversation() {
      const conversations = await loadJson<MessageConversationListResponse>(
        "/api/v1/messages/conversations?channel=assistant&limit=1",
        controller.signal,
      );
      const latestConversation = conversations?.items[0];
      if (!latestConversation) {
        setAssistantMessages([introMessage]);
        return;
      }

      const messageList = await loadJson<MessageListResponse>(
        `/api/v1/messages/conversations/${encodeURIComponent(latestConversation.id)}/messages?limit=50`,
        controller.signal,
      );
      if (!messageList?.items.length) {
        setConversationId(latestConversation.id);
        setAssistantMessages([introMessage]);
        return;
      }

      setConversationId(latestConversation.id);
      setAssistantMessages(messageList.items.map(mapStoredMessage));
    }

    void loadAssistantConversation();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [assistantMessages, assistantLoading]);

  const timelineRows = useMemo(
    () =>
      [
        ...dashboard.schedule.map((event) => ({ kind: "event" as const, ...event })),
        ...dashboard.todayTasks.map((task) => ({ kind: "task" as const, ...task })),
      ].sort((a, b) => a.time.localeCompare(b.time)),
    [dashboard.schedule, dashboard.todayTasks],
  );

  const completionPercent =
    dashboard.todayTaskCount > 0
      ? Math.round((dashboard.todayTasks.filter((task) => task.done).length / dashboard.todayTaskCount) * 100)
      : 0;

  const dayPulseItems = [
    { icon: CheckSquare, label: "Выполнено задач", value: `${dashboard.todayTasks.filter((task) => task.done).length} из ${dashboard.todayTaskCount}` },
    { icon: Calendar, label: "Встречи сегодня", value: String(dashboard.schedule.length) },
    { icon: Activity, label: "Задачи без времени", value: String(dashboard.backlogTasks.length) },
    { icon: Bot, label: "Ассистент", value: assistantLoading ? "думает" : "готов", accent: true },
  ];

  const timeLabel = new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: getBrowserTimeZone(),
  }).format(now);
  const dayMonthLabel = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: getBrowserTimeZone(),
  }).format(now);
  const weekdayLabel = new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    timeZone: getBrowserTimeZone(),
  }).format(now);

  const sendAssistantMessage = async () => {
    const message = assistantInput.trim();
    if (!message || assistantLoading) return;

    setAssistantInput("");
    setAssistantLoading(true);
    setAssistantMessages((prev) => [
      ...prev,
      { id: crypto.randomUUID(), from: "me", text: message, time: timeNow() },
    ]);

    try {
      const response = await apiClient("/api/v1/messages/assistant", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          conversation_id: conversationId,
          sandbox: "read-only",
          approval_policy: "on-request",
        }),
      });
      if (!response.ok) throw new Error(await readError(response));
      const body = (await response.json()) as AssistantChatResponse;
      setConversationId(body.conversation_id);
      setAssistantMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), from: "ai", text: body.message, time: timeNow() },
      ]);
    } catch (error) {
      setAssistantMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          from: "system",
          text: error instanceof Error ? error.message : "Не удалось получить ответ ассистента.",
          time: timeNow(),
        },
      ]);
    } finally {
      setAssistantLoading(false);
    }
  };

  return (
    <div className="h-full box-border overflow-y-auto bg-[#F6F8FC] px-4 py-4 sm:px-5 sm:py-5 xl:overflow-hidden">
      <div className="mx-auto grid max-w-[1600px] grid-cols-1 gap-4 xl:h-full xl:grid-cols-[minmax(0,2.12fr)_430px]">
        <div className="stoic-home-grid min-w-0 space-y-4 xl:space-y-0">
          <Card className="flex h-[88px] items-center justify-between px-5 sm:px-6">
            <div className="min-w-0 flex-1">
              <p className="truncate text-[18px] font-extrabold uppercase tracking-wide leading-tight text-[#142341]">
                У тебя всё получится
              </p>
              <p className="mt-1 truncate text-[13px] font-medium text-[#8B9ABA]">
                И в этом тебе поможет единая база для жизни и коммуникаций.
              </p>
            </div>

            <div className="ml-auto flex shrink-0 flex-col items-end">
              <p
                className="text-[44px] font-semibold leading-none tracking-tight text-[#142341]"
                style={{
                  textShadow: "0 0 18px rgba(47,115,255,0.45), 0 0 2px rgba(47,115,255,0.35)",
                }}
              >
                {timeLabel}
              </p>
              <p className="-mt-0.5 text-[11px] font-medium text-[#7182A8]">
                {dayMonthLabel}, {weekdayLabel}
              </p>
            </div>
          </Card>

          <div className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-[1.35fr_1fr]">
            <Card className="flex flex-col overflow-hidden xl:h-full">
              <div className="flex items-center gap-4 border-b border-[#E9EDF5] px-5 py-3">
                <Calendar size={18} className="text-[#7284AF]" />
                <h2 className="text-[17px] font-semibold text-[#142341]">План на сегодня</h2>
                <span className="flex items-center gap-2 text-[13px] font-medium text-[#7182A8]">
                  <Calendar size={14} />
                  {dayMonthLabel}, {weekdayLabel}
                </span>
              </div>

              <div className="flex min-h-0 flex-1 flex-col px-5 py-3">
                <div className="min-h-0 flex-1 overflow-y-auto pr-1">
                  {timelineRows.length > 0 ? (
                    <div className="space-y-1.5">
                      {timelineRows.map((item) => (
                        <div
                          key={`${item.kind}-${item.id}`}
                          className="grid grid-cols-[64px_1fr] gap-3 rounded-lg px-1 py-1 transition-colors hover:bg-[#F8FAFE]"
                        >
                          <div className="pt-0.5 text-[11.5px] font-semibold leading-tight text-[#253657]">{item.time}</div>
                          {item.kind === "event" ? (
                            <div className="flex gap-4">
                              <div className="w-[3px] rounded-full" style={{ background: item.color }} />
                              <div className="min-w-0">
                                <p className="truncate text-[12.5px] font-semibold leading-tight text-[#1A294A]">{item.title}</p>
                                {item.location && (
                                  <p className="truncate text-[10.5px] font-medium leading-tight text-[#8B9ABA]">{item.location}</p>
                                )}
                              </div>
                            </div>
                          ) : (
                            <div className="flex items-start gap-3">
                              <button
                                className={`mt-1 grid h-4 w-4 place-items-center rounded-[5px] border ${
                                  item.done ? "border-[#19CDB6] bg-[#19CDB6]" : "border-[#B9C5DC] bg-white"
                                }`}
                                aria-label={item.done ? "Задача выполнена" : "Задача"}
                              >
                                {item.done && <Check size={10} className="text-white" strokeWidth={3} />}
                              </button>
                              <p className="text-[12.5px] font-semibold leading-tight text-[#253657]">{item.text}</p>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyTile
                      title="На сегодня нет событий и задач"
                      description="Когда в базе появятся реальные задачи с deadline и события календаря, они будут показаны здесь."
                    />
                  )}
                </div>

                <div className="mt-3 border-t border-[#E9EDF5] pt-2">
                  <p className="mb-1 text-[10.5px] font-semibold text-[#253657]">Без времени</p>
                  {dashboard.backlogTasks.length > 0 ? (
                    <div className="space-y-1">
                      {dashboard.backlogTasks.map((task) => (
                        <div
                          key={task.id}
                          className="flex items-center gap-3 rounded-md py-0.5 text-[11.5px] font-semibold leading-tight text-[#8B9ABA]"
                        >
                          <span className="h-3.5 w-3.5 rounded-full border border-[#B9C5DC]" />
                          {task.text}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-[11.5px] font-medium leading-tight text-[#8B9ABA]">
                      В базе сейчас нет реальных задач без deadline.
                    </p>
                  )}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#E9EDF5] pt-3">
                  <button
                    onClick={() => navigate("/knowledge?tab=tasks")}
                    className="inline-flex h-[26px] items-center gap-2 rounded-lg border border-[#DDE5F2] bg-[#F8FAFE] px-3 text-[11px] font-semibold text-[#7182A8] hover:border-[#BFD0F2]"
                  >
                    <Plus size={14} />
                    Открыть задачи
                  </button>
                  <button
                    onClick={() => navigate("/knowledge?tab=calendar")}
                    className="inline-flex h-[26px] items-center gap-2 rounded-lg border border-[#DDE5F2] bg-[#F8FAFE] px-3 text-[11px] font-semibold text-[#7182A8] hover:border-[#BFD0F2]"
                  >
                    <Plus size={14} />
                    Открыть календарь
                  </button>
                  <button
                    onClick={() => navigate("/knowledge?tab=calendar")}
                    className="ml-auto inline-flex h-[26px] items-center gap-1 text-[11px] font-semibold text-[#2F73FF]"
                  >
                    Полный обзор дня
                    <ArrowRight size={14} />
                  </button>
                </div>
              </div>
            </Card>

            <Card className="flex flex-col overflow-hidden xl:h-full">
              <div className="flex items-center justify-between border-b border-[#E9EDF5] px-5 py-4">
                <h2 className="text-[17px] font-semibold text-[#142341]">Сообщения</h2>
                <button onClick={() => navigate("/messages")} className="text-[13px] font-semibold text-[#2F73FF]">
                  Открыть все
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                <div className="space-y-1">
                  {dashboard.telegramAuthorized ? (
                    dashboard.telegramDialogs.length > 0 ? (
                      dashboard.telegramDialogs.map((message) => (
                      <button
                        key={message.id}
                        onClick={() => navigate("/messages")}
                        className="flex w-full items-center gap-3 rounded-xl px-2 py-3 text-left transition-colors hover:bg-[#F8FAFE]"
                      >
                        <span
                          className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-[12px] font-bold text-white"
                          style={{ background: badgeColor(message.title) }}
                        >
                          {initials(message.title)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[14px] font-semibold text-[#1A294A]">{message.title}</span>
                          <span className="block truncate text-[12px] font-medium text-[#8B9ABA]">
                            {message.kind === "group" ? "Группа Telegram" : "Контакт Telegram"}
                          </span>
                        </span>
                        <span className="flex shrink-0 flex-col items-end gap-2">
                          <span className="text-[11px] font-medium text-[#7182A8]">Telegram</span>
                          {message.unread_count > 0 && (
                            <span className="grid h-5 min-w-5 place-items-center rounded-full bg-[#2F73FF] px-1.5 text-[10px] font-bold text-white">
                              {message.unread_count}
                            </span>
                          )}
                        </span>
                      </button>
                      ))
                    ) : (
                      <div className="rounded-xl border border-[#E9EDF5] bg-[#F8FAFE] px-4 py-4 text-[12px] text-[#7E8FB3]">
                        Telegram подключен, но новых диалогов после момента подключения пока нет.
                      </div>
                    )
                  ) : (
                    <div className="rounded-xl border border-[#E9EDF5] bg-[#F8FAFE] px-4 py-4 text-[12px] text-[#7E8FB3]">
                      Подключите Telegram в настройках, чтобы здесь появились реальные сообщения.
                    </div>
                  )}
                </div>
              </div>
            </Card>
          </div>

          <div className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-4">
            <Card className="flex min-h-[220px] flex-col overflow-hidden xl:min-h-0 xl:h-[220px]">
              <div className="flex items-center gap-3 border-b border-[#E9EDF5] px-4 py-3">
                <Activity size={18} className="text-[#2F73FF]" />
                <h3 className="text-[16px] font-semibold text-[#142341]">Пульс дня</h3>
              </div>
              <div className="flex min-h-0 flex-1 items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1 space-y-2">
                  {dayPulseItems.map(({ icon: Icon, label, value, accent }) => (
                    <div key={label} className="flex items-center gap-2">
                      <Icon size={15} className="shrink-0 text-[#2F73FF]" />
                      <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-[#253657]">{label}</span>
                      <span className={`shrink-0 text-[12px] font-semibold ${accent ? "text-[#17CDB8]" : "text-[#253657]"}`}>
                        {value}
                      </span>
                    </div>
                  ))}
                </div>
                <ProgressRing value={completionPercent} />
              </div>
              <div className="px-4 pb-3">
                <button className="flex h-8 w-full items-center justify-center gap-2 rounded-lg border border-[#DDE5F2] bg-[#F8FAFE] text-[12px] font-semibold text-[#7182A8]">
                  Открыть обзор
                  <ArrowRight size={14} />
                </button>
              </div>
            </Card>

            <Card className="flex min-h-[220px] flex-col overflow-hidden xl:min-h-0 xl:h-[220px]">
              <div className="flex items-center justify-between border-b border-[#E9EDF5] px-4 py-3">
                <div className="flex items-center gap-3">
                  <Briefcase size={18} className="text-[#7182A8]" />
                  <h3 className="text-[16px] font-semibold text-[#142341]">Проекты</h3>
                </div>
                <button onClick={() => navigate("/knowledge?tab=projects")} className="text-[13px] font-semibold text-[#2F73FF]">
                  Все
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1.5 px-4 py-2.5">
                {dashboard.projects.length > 0 ? (
                  dashboard.projects.map((project) => (
                    <button
                      key={project.id}
                      className="flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors hover:bg-[#F8FAFE]"
                    >
                      <FolderKanban size={15} className="shrink-0 text-[#2F73FF]" />
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-[#253657]">
                        {project.primary_name || project.description || project.id}
                      </span>
                    </button>
                  ))
                ) : (
                  <EmptyTile
                    title="Проектов пока нет"
                    description="В базе нет сущностей типа project. Этот блок снова наполнится, когда появятся реальные проекты."
                  />
                )}
              </div>
            </Card>

            <Card className="flex min-h-[220px] flex-col overflow-hidden xl:min-h-0 xl:h-[220px]">
              <div className="flex items-center justify-between border-b border-[#E9EDF5] px-4 py-3">
                <h3 className="text-[16px] font-semibold text-[#142341]">Заметки</h3>
                <button onClick={() => navigate("/knowledge?tab=notes")} className="text-[13px] font-semibold text-[#2F73FF]">
                  Все
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1.5 px-4 py-2.5">
                {dashboard.notes.length > 0 ? (
                  dashboard.notes.map((note) => (
                    <button
                      key={note.id}
                      className="flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors hover:bg-[#F8FAFE]"
                    >
                      <NotebookPen size={15} className="shrink-0 text-[#2F73FF]" />
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-[#253657]">
                        {extractSourceTitle(note)}
                      </span>
                    </button>
                  ))
                ) : (
                  <EmptyTile
                    title="Заметок пока нет"
                    description="Когда появятся реальные источники типа note, здесь будут последние сохранённые заметки."
                  />
                )}
              </div>
            </Card>

            <Card className="flex min-h-[220px] flex-col overflow-hidden xl:min-h-0 xl:h-[220px]">
              <div className="flex items-center justify-between border-b border-[#E9EDF5] px-4 py-3">
                <h3 className="text-[16px] font-semibold text-[#142341]">Недавние файлы</h3>
                <button onClick={() => navigate("/knowledge?tab=files")} className="text-[13px] font-semibold text-[#2F73FF]">
                  Все
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1.5 px-4 py-2.5">
                {dashboard.files.length > 0 ? (
                  dashboard.files.map((file) => (
                    <button
                      key={file.id}
                      className="flex w-full items-center gap-2 rounded-lg px-1.5 py-1.5 text-left transition-colors hover:bg-[#F8FAFE]"
                    >
                      <FileText size={15} className="shrink-0 text-[#2F73FF]" />
                      <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-[#253657]">
                        {extractSourceTitle(file)}
                      </span>
                    </button>
                  ))
                ) : (
                  <EmptyTile
                    title="Файловых источников пока нет"
                    description="Когда появятся реальные source_type=file, они будут отображаться в этом блоке."
                  />
                )}
              </div>
            </Card>
          </div>
        </div>

        <aside className="min-w-0 xl:h-full">
          <Card className="flex flex-col overflow-hidden xl:h-full">
            <div className="flex items-center justify-between border-b border-[#E9EDF5] px-5 py-4">
              <div className="flex items-center gap-3">
                <div className="grid h-8 w-8 place-items-center rounded-xl bg-[#EEF3FE]">
                  <img src={stoicLogo} alt="Stoic" className="h-5 w-5 object-contain" />
                </div>
                <h2 className="text-[17px] font-semibold text-[#142341]">ИИ-ассистент</h2>
              </div>
              <button className="text-[#7182A8]" aria-label="Меню ассистента">
                <MoreVertical size={18} />
              </button>
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto px-5 py-3">
              {assistantMessages.map((message) => (
                <div
                  key={message.id}
                  className={
                    message.from === "me"
                      ? "ml-auto max-w-[78%] rounded-xl bg-[#EEF3FE] px-4 py-2 text-[13px] font-medium text-[#253657]"
                      : message.from === "system"
                        ? "max-w-[82%] rounded-xl border border-[#F1C4B8] bg-[#FFF3EE] px-4 py-2 text-[13px] font-medium text-[#9E4A33]"
                        : "max-w-[78%] rounded-xl bg-[#F5F7FC] px-4 py-2 text-[13px] font-medium text-[#253657]"
                  }
                >
                  {message.from !== "me" && message.from !== "system" && (
                    <div className="flex gap-2">
                      <img src={stoicLogo} alt="Stoic" className="mt-0.5 h-4 w-4 shrink-0 object-contain" />
                      <span>{message.text}</span>
                    </div>
                  )}
                  {message.from === "me" && message.text}
                  {message.from === "system" && message.text}
                  <p
                    className={`mt-1 text-right text-[11px] ${
                      message.from === "me"
                        ? "text-[#6E8FE0]"
                        : message.from === "system"
                          ? "text-[#C78269]"
                          : "text-[#8B9ABA]"
                    }`}
                  >
                    {message.time}
                  </p>
                </div>
              ))}

              {assistantLoading && (
                <div className="max-w-[78%] rounded-xl bg-[#F5F7FC] px-4 py-2 text-[13px] font-medium text-[#253657]">
                  <div className="flex gap-2">
                    <img src={stoicLogo} alt="Stoic" className="mt-0.5 h-4 w-4 shrink-0 object-contain" />
                    <span>Думаю над ответом...</span>
                  </div>
                </div>
              )}
              <div ref={chatBottomRef} />
            </div>

            <div className="border-t border-[#E9EDF5] px-5 py-4">
              <div className="flex h-10 items-center rounded-lg border border-[#DDE5F2] bg-[#FAFBFE] px-3">
                <input
                  value={assistantInput}
                  onChange={(event) => setAssistantInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void sendAssistantMessage();
                    }
                  }}
                  className="min-w-0 flex-1 bg-transparent text-[13px] text-[#253657] outline-none placeholder:text-[#9AA8C5]"
                  placeholder="Написать запрос..."
                />
                <button
                  onClick={() => void sendAssistantMessage()}
                  disabled={!assistantInput.trim() || assistantLoading}
                  className="text-[#2F73FF] disabled:cursor-not-allowed disabled:text-[#AAB6D1]"
                  aria-label="Отправить запрос ассистенту"
                >
                  <Send size={17} />
                </button>
              </div>
            </div>
          </Card>
        </aside>
      </div>

      <style>{`
        @media (min-width: 1280px) {
          .stoic-home-grid {
            display: grid;
            gap: 16px;
            grid-template-rows: 88px minmax(0, 1fr) 220px;
          }
        }

        @media (min-width: 1280px) and (min-height: 980px) {
          .stoic-home-grid {
            grid-template-rows: 88px minmax(0, 1fr) 220px;
          }
        }
      `}</style>
    </div>
  );
}

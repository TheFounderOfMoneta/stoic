import { useState, useRef, useEffect } from "react";
import {
  Search, Sun, Bell, Minus, Square, X, PanelLeft, Menu,
  MessageSquare, CheckSquare, Calendar, Info, Check, Trash2,
} from "lucide-react";

interface Notification {
  id: number;
  type: "message" | "task" | "calendar" | "system";
  title: string;
  desc: string;
  time: string;
  read: boolean;
}

const INITIAL_NOTIFICATIONS: Notification[] = [
  { id: 1, type: "message",  title: "Алексей Петров",       desc: "Давай обсудим детали завтра?",               time: "2 мин",   read: false },
  { id: 2, type: "task",     title: "Задача: презентация",   desc: "Через 1 час — дедлайн по клиенту",           time: "5 мин",   read: false },
  { id: 3, type: "calendar", title: "Встреча в 12:00",       desc: "Обсуждение проекта · Офис",                  time: "30 мин",  read: false },
  { id: 4, type: "message",  title: "Команда маркетинга",    desc: "Анна: Отличная работа всей командой!",       time: "1 ч",     read: false },
  { id: 5, type: "task",     title: "Задача выполнена",      desc: "«Написать статью» отмечена как готовая",     time: "2 ч",     read: true  },
  { id: 6, type: "calendar", title: "Завтра в 10:00",        desc: "Ежедневная планёрка · Онлайн",               time: "3 ч",     read: true  },
  { id: 7, type: "system",   title: "Хранилище заполнено",   desc: "Использовано 64 ГБ из 100 ГБ",              time: "вчера",   read: true  },
];

const TYPE_ICON: Record<Notification["type"], React.ComponentType<{ size?: number; className?: string }>> = {
  message:  MessageSquare,
  task:     CheckSquare,
  calendar: Calendar,
  system:   Info,
};

const TYPE_COLOR: Record<Notification["type"], string> = {
  message:  "#4B78F5",
  task:     "#00B894",
  calendar: "#6C5CE7",
  system:   "#FD9644",
};

const TYPE_BG: Record<Notification["type"], string> = {
  message:  "#EEF3FE",
  task:     "#E6FAF5",
  calendar: "#F0EEFF",
  system:   "#FFF4EB",
};

interface TopBarProps {
  collapsed: boolean;
  onToggleCollapse: () => void;
  onMobileMenu: () => void;
}

export function StoicTopBar({ collapsed, onToggleCollapse, onMobileMenu }: TopBarProps) {
  const [notifOpen, setNotifOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>(INITIAL_NOTIFICATIONS);
  const panelRef = useRef<HTMLDivElement>(null);
  const bellRef = useRef<HTMLButtonElement>(null);

  const unreadCount = notifications.filter((n) => !n.read).length;

  // Close on outside click
  useEffect(() => {
    if (!notifOpen) return;
    const handle = (e: MouseEvent) => {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        bellRef.current && !bellRef.current.contains(e.target as Node)
      ) {
        setNotifOpen(false);
      }
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [notifOpen]);

  const markAllRead = () =>
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));

  const markRead = (id: number) =>
    setNotifications((prev) =>
      prev.map((n) => (n.id === id ? { ...n, read: true } : n))
    );

  const dismiss = (id: number) =>
    setNotifications((prev) => prev.filter((n) => n.id !== id));

  const clearAll = () => setNotifications([]);

  return (
    <div
      className="flex items-center gap-3 px-4 sm:px-6 bg-white border-b border-[#E9EDF5] flex-shrink-0"
      style={{ height: 72 }}
    >
      {/* Mobile hamburger */}
      <button
        onClick={onMobileMenu}
        className="lg:hidden w-8 h-8 flex items-center justify-center rounded-xl text-gray-500 hover:bg-gray-100 transition-colors flex-shrink-0"
      >
        <Menu size={18} />
      </button>

      {/* Desktop collapse toggle */}
      <button
        onClick={onToggleCollapse}
        className="hidden lg:flex xl:hidden w-8 h-8 items-center justify-center rounded-xl text-[#8B9ABA] hover:bg-gray-100 transition-colors flex-shrink-0"
        title={collapsed ? "Развернуть" : "Свернуть"}
      >
        <PanelLeft size={16} />
      </button>

      {/* Search */}
      <div className="relative ml-0 h-10 w-full max-w-[430px]">
        <Search size={15} className="absolute left-4 top-1/2 -translate-y-1/2 text-[#8B9ABA]" />
        <input
          type="text"
          placeholder="Поиск по всей жизни..."
          className="h-full w-full rounded-[18px] border border-[#EEF2F8] bg-[#F7F9FD] pl-11 pr-12 text-[14px] font-medium text-[#253657] outline-none transition-all placeholder:text-[#8B9ABA] focus:border-blue-200 focus:bg-white"
        />
        <kbd className="absolute right-4 top-1/2 -translate-y-1/2 text-[12px] text-[#7182A8] font-medium hidden sm:block">
          ⌘K
        </kbd>
      </div>

      <div className="flex-1" />

      {/* Icons */}
      <div className="flex items-center gap-1 relative">
        <button className="w-9 h-9 flex items-center justify-center rounded-xl text-[#7182A8] hover:text-[#2F73FF] hover:bg-gray-50 transition-colors" aria-label="Переключить тему">
          <Sun size={17} />
        </button>

        {/* Bell */}
        <button
          ref={bellRef}
          onClick={() => setNotifOpen((o) => !o)}
          className={`w-9 h-9 flex items-center justify-center rounded-xl transition-colors relative ${
            notifOpen ? "bg-[#EEF3FE] text-[#2F73FF]" : "text-[#7182A8] hover:text-[#2F73FF] hover:bg-gray-50"
          }`}
          aria-label="Уведомления"
        >
          <Bell size={17} />
          {unreadCount > 0 && (
            <span
              className="absolute top-1 right-1 min-w-[14px] h-[14px] rounded-full bg-[#2F73FF] text-white text-[8px] flex items-center justify-center px-0.5 font-bold"
              style={{ outline: "2px solid white" }}
            >
              {unreadCount}
            </span>
          )}
        </button>

        {/* Notifications panel */}
        {notifOpen && (
          <div
            ref={panelRef}
            className="absolute top-[calc(100%+8px)] right-0 z-[9999] bg-white rounded-2xl overflow-hidden"
            style={{
              width: 340,
              boxShadow: "0 12px 40px rgba(0,0,0,0.13), 0 2px 8px rgba(0,0,0,0.07)",
              border: "1px solid rgba(0,0,0,0.07)",
            }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 pt-3.5 pb-3 border-b border-gray-50">
              <div className="flex items-center gap-2">
                <span className="text-[13.5px] text-gray-800 font-semibold">Уведомления</span>
                {unreadCount > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full bg-[#EEF3FE] text-[#4B78F5] text-[10px] font-semibold">
                    {unreadCount} новых
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                {unreadCount > 0 && (
                  <button
                    onClick={markAllRead}
                    className="flex items-center gap-1 text-[11px] text-[#4B78F5] hover:underline px-2 py-1 rounded-lg hover:bg-blue-50 transition-colors"
                  >
                    <Check size={11} />
                    Прочитать все
                  </button>
                )}
                <button
                  onClick={clearAll}
                  className="w-6 h-6 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 transition-colors"
                  title="Очистить всё"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            </div>

            {/* List */}
            <div className="overflow-y-auto" style={{ maxHeight: 380 }}>
              {notifications.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-400">
                  <Bell size={28} className="mb-3 opacity-30" />
                  <p className="text-[13px]">Нет уведомлений</p>
                </div>
              ) : (
                notifications.map((n) => {
                  const Icon = TYPE_ICON[n.type];
                  return (
                    <div
                      key={n.id}
                      onClick={() => markRead(n.id)}
                      className={`flex items-start gap-3 px-4 py-3 cursor-pointer transition-colors border-b border-gray-50 last:border-0 group ${
                        n.read ? "hover:bg-gray-50" : "bg-blue-50/40 hover:bg-blue-50/60"
                      }`}
                    >
                      <div
                        className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 mt-0.5"
                        style={{ background: TYPE_BG[n.type] }}
                      >
                        <Icon size={14} style={{ color: TYPE_COLOR[n.type] }} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className={`text-[12.5px] truncate ${n.read ? "text-gray-700" : "text-gray-900 font-semibold"}`}>
                            {n.title}
                          </p>
                          <span className="text-[10px] text-gray-400 flex-shrink-0 mt-0.5">{n.time}</span>
                        </div>
                        <p className="text-[11.5px] text-gray-400 mt-0.5 line-clamp-2">{n.desc}</p>
                      </div>
                      {!n.read && (
                        <div className="w-1.5 h-1.5 rounded-full bg-[#4B78F5] flex-shrink-0 mt-2" />
                      )}
                      <button
                        onClick={(e) => { e.stopPropagation(); dismiss(n.id); }}
                        className="w-5 h-5 flex items-center justify-center rounded-md text-gray-300 hover:text-gray-500 hover:bg-gray-100 opacity-0 group-hover:opacity-100 transition-all flex-shrink-0"
                      >
                        <X size={10} />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>

      {/* Window controls */}
      <div className="hidden sm:flex items-center gap-3 ml-3 pl-4 border-l border-[#E9EDF5]">
        <button className="w-7 h-7 flex items-center justify-center rounded-md text-[#7182A8] hover:text-[#253657] hover:bg-gray-100" aria-label="Свернуть окно">
          <Minus size={12} />
        </button>
        <button className="w-7 h-7 flex items-center justify-center rounded-md text-[#7182A8] hover:text-[#253657] hover:bg-gray-100" aria-label="Развернуть окно">
          <Square size={10} />
        </button>
        <button className="w-7 h-7 flex items-center justify-center rounded-md text-[#7182A8] hover:text-red-500 hover:bg-red-50" aria-label="Закрыть окно">
          <X size={12} />
        </button>
      </div>
    </div>
  );
}

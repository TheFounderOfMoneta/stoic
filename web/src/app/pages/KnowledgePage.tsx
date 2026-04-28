import { useState, useEffect } from "react";
import { useSearchParams } from "react-router";
import { apiClient } from "@/app/apiClient";
import {
  FileText,
  Users,
  FolderOpen,
  CheckSquare,
  Calendar,
  StickyNote,
  Plus,
  Search,
  Grid,
  List,
  Upload,
  MoreHorizontal,
  Check,
  ChevronLeft,
  ChevronRight,
  Star,
  Trash2,
  Filter,
  Tag,
  Clock,
  X,
  Mail,
  Phone,
  MapPin,
  Linkedin,
  Send,
  ExternalLink,
  ArrowLeft,
} from "lucide-react";

// ─── Types ───────────────────────────────────────────────────────────────────
interface Tab {
  id: string;
  label: string;
  icon: React.ComponentType<{
    size?: number;
    className?: string;
  }>;
}

const TABS: Tab[] = [
  { id: "files", label: "Файлы", icon: FileText },
  { id: "contacts", label: "Контакты", icon: Users },
  { id: "projects", label: "Проекты", icon: FolderOpen },
  { id: "tasks", label: "Задачи", icon: CheckSquare },
  { id: "calendar", label: "Календарь", icon: Calendar },
  { id: "notes", label: "Заметки", icon: StickyNote },
];

// ─── Files Tab ────────────────────────────────────────────────────────────────
const FOLDERS = [
  {
    id: 1,
    name: "Документы",
    count: 34,
    color: "#4B78F5",
    icon: "📄",
  },
  {
    id: 2,
    name: "Изображения",
    count: 127,
    color: "#00B894",
    icon: "🖼️",
  },
  {
    id: 3,
    name: "Видео",
    count: 18,
    color: "#6C5CE7",
    icon: "🎬",
  },
  {
    id: 4,
    name: "Архивы",
    count: 9,
    color: "#FD9644",
    icon: "📦",
  },
  {
    id: 5,
    name: "Таблицы",
    count: 22,
    color: "#E17055",
    icon: "📊",
  },
  {
    id: 6,
    name: "Презентации",
    count: 15,
    color: "#A29BFE",
    icon: "📋",
  },
];

const FILES = [
  {
    id: 1,
    name: "Презентация_проект.pdf",
    size: "12.4 МБ",
    date: "22 апр",
    type: "pdf",
    color: "#E17055",
  },
  {
    id: 2,
    name: "Финансовый_отчет_Q1.xlsx",
    size: "18.7 КБ",
    date: "21 апр",
    type: "xlsx",
    color: "#00B894",
  },
  {
    id: 3,
    name: "Стратегия_2026.docx",
    size: "34.1 КБ",
    date: "20 апр",
    type: "docx",
    color: "#4B78F5",
  },
  {
    id: 4,
    name: "Фото_конференция.jpg",
    size: "4.2 МБ",
    date: "19 апр",
    type: "jpg",
    color: "#6C5CE7",
  },
  {
    id: 5,
    name: "Макет_лендинга.fig",
    size: "8.9 МБ",
    date: "18 апр",
    type: "fig",
    color: "#FD9644",
  },
  {
    id: 6,
    name: "База_знаний_backup.zip",
    size: "102 МБ",
    date: "15 апр",
    type: "zip",
    color: "#A29BFE",
  },
];

function FilesTab() {
  const [view, setView] = useState<"grid" | "list">("grid");
  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              placeholder="Поиск файлов..."
              className="pl-8 pr-3 py-2 text-[12.5px] bg-white border border-gray-200 rounded-xl outline-none focus:border-blue-300 w-52"
            />
          </div>
          <button className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-white border border-gray-200 text-[12.5px] text-gray-600 hover:bg-gray-50">
            <Filter size={13} />
            Фильтры
          </button>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex bg-gray-100 rounded-xl p-0.5">
            {(["grid", "list"] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                className={`p-1.5 rounded-lg transition-colors ${view === v ? "bg-white shadow-sm" : "text-gray-400"}`}
              >
                {v === "grid" ? (
                  <Grid size={14} />
                ) : (
                  <List size={14} />
                )}
              </button>
            ))}
          </div>
          <button
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-white text-[12.5px] font-medium"
            style={{
              background:
                "linear-gradient(135deg, #4B78F5, #6C5CE7)",
            }}
          >
            <Upload size={13} />
            Загрузить
          </button>
        </div>
      </div>

      <div>
        <h3 className="text-[12.5px] text-gray-500 font-semibold mb-3">
          Папки
        </h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
          {FOLDERS.map((f) => (
            <button
              key={f.id}
              className="bg-white rounded-2xl p-4 text-left hover:border-blue-100 border border-gray-100 transition-all hover:shadow-md group"
            >
              <div className="text-2xl mb-2">{f.icon}</div>
              <p className="text-[12.5px] text-gray-800 font-medium truncate">
                {f.name}
              </p>
              <p className="text-[11px] text-gray-400 mt-0.5">
                {f.count} файлов
              </p>
            </button>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-[12.5px] text-gray-500 font-semibold mb-3">
          Недавние файлы
        </h3>
        {view === "grid" ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 gap-3">
            {FILES.map((f) => (
              <div
                key={f.id}
                className="bg-white rounded-2xl border border-gray-100 hover:border-blue-100 hover:shadow-md transition-all cursor-pointer group"
              >
                <div
                  className="h-24 rounded-t-2xl flex items-center justify-center"
                  style={{ background: `${f.color}12` }}
                >
                  <FileText
                    size={32}
                    style={{ color: f.color }}
                    strokeWidth={1.5}
                  />
                </div>
                <div className="p-3">
                  <p className="text-[12px] text-gray-800 font-medium truncate">
                    {f.name}
                  </p>
                  <div className="flex items-center justify-between mt-1">
                    <span className="text-[10.5px] text-gray-400">
                      {f.size}
                    </span>
                    <span className="text-[10.5px] text-gray-400">
                      {f.date}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
            {FILES.map((f, i) => (
              <div
                key={f.id}
                className={`flex items-center gap-3 px-4 py-3 hover:bg-gray-50 cursor-pointer transition-colors ${i < FILES.length - 1 ? "border-b border-gray-50" : ""}`}
              >
                <div
                  className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{ background: `${f.color}18` }}
                >
                  <FileText
                    size={15}
                    style={{ color: f.color }}
                  />
                </div>
                <p className="flex-1 text-[13px] text-gray-800 font-medium truncate">
                  {f.name}
                </p>
                <span className="text-[11.5px] text-gray-400">
                  {f.size}
                </span>
                <span className="text-[11.5px] text-gray-400 w-16 text-right">
                  {f.date}
                </span>
                <button className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100">
                  <MoreHorizontal size={14} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Contacts Tab ─────────────────────────────────────────────────────────────
interface ContactItem {
  id: number;
  name: string;
  role: string;
  company: string;
  email: string;
  phone: string;
  color: string;
  avatar: string;
  tag: string;
  lastContact: string;
  telegram?: string;
  linkedin?: string;
  city?: string;
  birthday?: string;
  note?: string;
  projects?: { name: string; progress: number; color: string }[];
  recentMessages?: { text: string; time: string; from: "me" | "other" }[];
}

const CONTACTS_DATA: ContactItem[] = [
  {
    id: 1,
    name: "Алексей Петров",
    role: "Frontend Developer",
    company: "Tech Corp",
    email: "alex@techcorp.ru",
    phone: "+7 (900) 123-45-67",
    color: "#4B78F5",
    avatar: "АП",
    tag: "Работа",
    lastContact: "22 апр",
    telegram: "@alex_petrov",
    linkedin: "linkedin.com/in/alexpetrov",
    city: "Москва",
    birthday: "15 марта",
    note: "Работаем вместе над редизайном сайта и интеграцией с CRM. Очень ответственный и профессиональный. Предпочитает общение через Telegram.",
    projects: [
      { name: "Редизайн сайта", progress: 65, color: "#4B78F5" },
      { name: "Интеграция с CRM", progress: 45, color: "#00B894" },
    ],
    recentMessages: [
      { text: "Привет, Влад! Смотрел предложения по сайту?", time: "10:15, 22 апр", from: "other" },
      { text: "Да, всё изучил. Мне нравится направление.", time: "10:18, 22 апр", from: "me" },
      { text: "Отличная идея! Согласен с предложенным подходом.", time: "10:24, 22 апр", from: "other" },
    ],
  },
  {
    id: 2,
    name: "Марина Иванова",
    role: "UX Designer",
    company: "Design Studio",
    email: "marina@design.ru",
    phone: "+7 (915) 234-56-78",
    color: "#00B894",
    avatar: "МИ",
    tag: "Партнёр",
    lastContact: "21 апр",
  },
  {
    id: 3,
    name: "Игорь Смирнов",
    role: "Product Manager",
    company: "StartupXYZ",
    email: "igor@startup.ru",
    phone: "+7 (926) 345-67-89",
    color: "#FD9644",
    avatar: "ИС",
    tag: "Клиент",
    lastContact: "20 апр",
  },
  {
    id: 4,
    name: "Ольга Захарова",
    role: "Marketing Lead",
    company: "MarketPlus",
    email: "olga@market.ru",
    phone: "+7 (903) 456-78-90",
    color: "#A29BFE",
    avatar: "ОЗ",
    tag: "Работа",
    lastContact: "19 апр",
  },
  {
    id: 5,
    name: "Дмитрий Козлов",
    role: "CEO",
    company: "BizGroup",
    email: "dmitry@biz.ru",
    phone: "+7 (916) 567-89-01",
    color: "#0984E3",
    avatar: "ДК",
    tag: "Клиент",
    lastContact: "18 апр",
  },
  {
    id: 6,
    name: "Анна Белова",
    role: "Content Writer",
    company: "Media Hub",
    email: "anna@media.ru",
    phone: "+7 (999) 678-90-12",
    color: "#E17055",
    avatar: "АБ",
    tag: "Партнёр",
    lastContact: "15 апр",
  },
];
const CONTACT_TAGS = ["Все", "Работа", "Клиент", "Партнёр"];

// ─── Contact Detail View ──────────────────────────────────────────────────────
function ContactDetail({ contact, onBack }: { contact: ContactItem; onBack: () => void }) {
  return (
    <div className="p-5 space-y-4 max-w-3xl mx-auto">
      {/* Back */}
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-[12.5px] text-gray-500 hover:text-gray-800 transition-colors"
      >
        <ArrowLeft size={14} /> Все контакты
      </button>

      {/* Header card */}
      <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden" style={{ boxShadow: "0 1px 6px rgba(0,0,0,0.07)" }}>
        <div className="h-20 w-full" style={{ background: `linear-gradient(135deg, ${contact.color}35, ${contact.color}12)` }} />
        <div className="px-6 pb-5 -mt-8">
          <div className="flex items-end justify-between gap-2">
            <div
              className="w-16 h-16 rounded-2xl border-4 border-white flex items-center justify-center text-white text-[18px] font-bold flex-shrink-0"
              style={{ background: contact.color, boxShadow: `0 4px 14px ${contact.color}50` }}
            >
              {contact.avatar}
            </div>
            <div className="flex items-center gap-2 mb-1 flex-wrap justify-end">
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[12px] font-medium border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors">
                <Phone size={12} /> Позвонить
              </button>
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[12px] font-medium text-white"
                style={{ background: `linear-gradient(135deg, ${contact.color}, #6C5CE7)` }}
              >
                <Send size={12} /> Написать
              </button>
            </div>
          </div>
          <div className="mt-3">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-[17px] text-gray-900" style={{ fontWeight: 700 }}>{contact.name}</h2>
              <span
                className="text-[10.5px] px-2 py-0.5 rounded-full font-medium"
                style={{ background: `${contact.color}18`, color: contact.color }}
              >
                {contact.tag}
              </span>
            </div>
            <p className="text-[13px] text-gray-500 mt-0.5">{contact.role} · {contact.company}</p>
            <div className="flex items-center gap-4 mt-2 flex-wrap">
              {contact.city && (
                <span className="flex items-center gap-1 text-[11.5px] text-gray-400">
                  <MapPin size={11} /> {contact.city}
                </span>
              )}
              <span className="flex items-center gap-1 text-[11.5px] text-gray-400">
                <Clock size={11} /> Последний контакт: {contact.lastContact}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Two-column body */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Left */}
        <div className="space-y-4">
          {/* Contact details */}
          <div className="bg-white rounded-2xl border border-gray-100 p-4" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
            <h3 className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide mb-3">Контакты</h3>
            <div className="space-y-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#EEF3FE] flex items-center justify-center flex-shrink-0">
                  <Mail size={13} className="text-[#4B78F5]" />
                </div>
                <div>
                  <p className="text-[10px] text-gray-400">Email</p>
                  <p className="text-[12.5px] text-gray-800 font-medium">{contact.email}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-[#E6FAF5] flex items-center justify-center flex-shrink-0">
                  <Phone size={13} className="text-[#00B894]" />
                </div>
                <div>
                  <p className="text-[10px] text-gray-400">Телефон</p>
                  <p className="text-[12.5px] text-gray-800 font-medium">{contact.phone}</p>
                </div>
              </div>
              {contact.telegram && (
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-sm" style={{ background: "#E8F4FD" }}>
                    ✈️
                  </div>
                  <div>
                    <p className="text-[10px] text-gray-400">Telegram</p>
                    <p className="text-[12.5px] text-gray-800 font-medium">{contact.telegram}</p>
                  </div>
                </div>
              )}
              {contact.linkedin && (
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center flex-shrink-0">
                    <Linkedin size={13} className="text-blue-600" />
                  </div>
                  <div>
                    <p className="text-[10px] text-gray-400">LinkedIn</p>
                    <p className="text-[12.5px] font-medium flex items-center gap-1" style={{ color: contact.color }}>
                      {contact.linkedin} <ExternalLink size={10} />
                    </p>
                  </div>
                </div>
              )}
              {contact.birthday && (
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-pink-50 flex items-center justify-center flex-shrink-0 text-sm">🎂</div>
                  <div>
                    <p className="text-[10px] text-gray-400">День рождения</p>
                    <p className="text-[12.5px] text-gray-800 font-medium">{contact.birthday}</p>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Note */}
          {contact.note && (
            <div className="bg-white rounded-2xl border border-gray-100 p-4" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
              <h3 className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide mb-2">Заметка</h3>
              <p className="text-[13px] text-gray-600 leading-relaxed">{contact.note}</p>
            </div>
          )}

          {/* Tags */}
          <div className="bg-white rounded-2xl border border-gray-100 p-4" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
            <h3 className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide mb-3">Метки</h3>
            <div className="flex flex-wrap gap-2">
              {[contact.tag, contact.company, contact.role].map((t, i) => (
                <span
                  key={i}
                  className="text-[11.5px] px-2.5 py-1 rounded-full border font-medium"
                  style={{ color: contact.color, borderColor: `${contact.color}30`, background: `${contact.color}0D` }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>

        {/* Right */}
        <div className="space-y-4">
          {/* Projects */}
          {contact.projects && contact.projects.length > 0 && (
            <div className="bg-white rounded-2xl border border-gray-100 p-4" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
              <h3 className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide mb-3">Совместные проекты</h3>
              <div className="space-y-3">
                {contact.projects.map((p, i) => (
                  <div key={i}>
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-[12.5px] text-gray-800 font-medium">{p.name}</p>
                      <span className="text-[11px] font-semibold" style={{ color: p.color }}>{p.progress}%</span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                      <div className="h-full rounded-full" style={{ width: `${p.progress}%`, background: p.color }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent messages */}
          {contact.recentMessages && contact.recentMessages.length > 0 && (
            <div className="bg-white rounded-2xl border border-gray-100 p-4" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[11px] text-gray-400 font-semibold uppercase tracking-wide">Последние сообщения</h3>
                <button className="text-[11px] hover:underline" style={{ color: contact.color }}>Открыть чат</button>
              </div>
              <div className="space-y-2">
                {contact.recentMessages.map((msg, i) => (
                  <div key={i} className={`flex ${msg.from === "me" ? "justify-end" : "justify-start"}`}>
                    <div
                      className="max-w-[85%] px-3 py-2 rounded-xl text-[12px] leading-relaxed"
                      style={
                        msg.from === "me"
                          ? { background: contact.color, color: "white", borderBottomRightRadius: 4 }
                          : { background: "#F3F4F8", color: "#374151", borderBottomLeftRadius: 4 }
                      }
                    >
                      <p>{msg.text}</p>
                      <p className={`text-[9.5px] mt-0.5 ${msg.from === "me" ? "text-white/60 text-right" : "text-gray-400"}`}>{msg.time}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ContactsTab() {
  const [tag, setTag] = useState("Все");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<ContactItem | null>(null);

  if (selected) {
    return <ContactDetail contact={selected} onBack={() => setSelected(null)} />;
  }

  const filtered = CONTACTS_DATA.filter(
    (c) =>
      (tag === "Все" || c.tag === tag) &&
      (!search || c.name.toLowerCase().includes(search.toLowerCase())),
  );
  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск контактов..."
              className="pl-8 pr-3 py-2 text-[12.5px] bg-white border border-gray-200 rounded-xl outline-none w-52"
            />
          </div>
          <div className="flex gap-1.5">
            {CONTACT_TAGS.map((t) => (
              <button
                key={t}
                onClick={() => setTag(t)}
                className={`px-3 py-1.5 rounded-full text-[11.5px] transition-colors ${tag === t ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <button
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-white text-[12.5px] font-medium"
          style={{ background: "linear-gradient(135deg, #4B78F5, #6C5CE7)" }}
        >
          <Plus size={13} /> Добавить
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map((c) => (
          <div
            key={c.id}
            onClick={() => setSelected(c)}
            className="bg-white rounded-2xl p-4 border border-gray-100 hover:border-blue-100 hover:shadow-md transition-all cursor-pointer group"
          >
            <div className="flex items-start gap-3">
              <div
                className="w-11 h-11 rounded-full flex items-center justify-center text-white font-bold flex-shrink-0"
                style={{ background: c.color }}
              >
                {c.avatar}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-[13.5px] text-gray-800 font-semibold truncate">{c.name}</p>
                <p className="text-[11.5px] text-gray-500">{c.role}</p>
                <p className="text-[11px] text-gray-400">{c.company}</p>
              </div>
              <span
                className="text-[10px] px-2 py-0.5 rounded-full font-medium"
                style={{ background: `${c.color}18`, color: c.color }}
              >
                {c.tag}
              </span>
            </div>
            <div className="mt-3 pt-3 border-t border-gray-50 space-y-1">
              <p className="text-[11.5px] text-gray-500 truncate">✉️ {c.email}</p>
              <p className="text-[11.5px] text-gray-500">📱 {c.phone}</p>
            </div>
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[10.5px] text-gray-400">Последний контакт: {c.lastContact}</span>
              <button
                onClick={(e) => e.stopPropagation()}
                className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100"
              >
                <MoreHorizontal size={13} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Projects Tab ─────────────────────────────────────────────────────────────
const PROJECTS = [
  {
    id: 1,
    name: "Редизайн сайта",
    status: "В работе",
    progress: 65,
    deadline: "30 апр",
    members: ["АП", "МИ"],
    color: "#4B78F5",
    priority: "Высокий",
  },
  {
    id: 2,
    name: "Запуск подкаста",
    status: "Бэклог",
    progress: 20,
    deadline: "15 мая",
    members: ["АБ"],
    color: "#6C5CE7",
    priority: "Средний",
  },
  {
    id: 3,
    name: "Финансовый отчёт Q1",
    status: "На проверке",
    progress: 90,
    deadline: "25 апр",
    members: ["ДК", "ОЗ"],
    color: "#FD9644",
    priority: "Высокий",
  },
  {
    id: 4,
    name: "Интеграция с CRM",
    status: "В работе",
    progress: 45,
    deadline: "10 мая",
    members: ["ИС", "АП"],
    color: "#00B894",
    priority: "Средний",
  },
  {
    id: 5,
    name: "Маркетинговая стратегия",
    status: "Завершён",
    progress: 100,
    deadline: "18 апр",
    members: ["МИ", "ОЗ", "АБ"],
    color: "#A29BFE",
    priority: "Низкий",
  },
  {
    id: 6,
    name: "Мобильное приложение",
    status: "Бэклог",
    progress: 5,
    deadline: "1 июн",
    members: ["АП"],
    color: "#E17055",
    priority: "Средний",
  },
];
const STATUSES = [
  "Все",
  "Бэклог",
  "В работе",
  "На проверке",
  "Завершён",
];
const PRIORITY_COLORS: Record<string, string> = {
  Высокий: "#E17055",
  Средний: "#FD9644",
  Низкий: "#00B894",
};

function ProjectsTab() {
  const [filter, setFilter] = useState("Все");
  const filtered =
    filter === "Все"
      ? PROJECTS
      : PROJECTS.filter((p) => p.status === filter);
  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-1.5 flex-wrap">
          {STATUSES.map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`px-3 py-1.5 rounded-full text-[11.5px] transition-colors ${filter === s ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}
            >
              {s}
            </button>
          ))}
        </div>
        <button
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-white text-[12.5px] font-medium"
          style={{
            background:
              "linear-gradient(135deg, #4B78F5, #6C5CE7)",
          }}
        >
          <Plus size={13} />
          Новый проект
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map((p) => (
          <div
            key={p.id}
            className="bg-white rounded-2xl p-4 border border-gray-100 hover:border-blue-100 hover:shadow-md transition-all cursor-pointer"
          >
            <div className="flex items-start justify-between mb-3">
              <div>
                <p className="text-[13.5px] text-gray-800 font-semibold">
                  {p.name}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <span
                    className="text-[10.5px] px-2 py-0.5 rounded-full font-medium"
                    style={{
                      background: `${PRIORITY_COLORS[p.priority]}18`,
                      color: PRIORITY_COLORS[p.priority],
                    }}
                  >
                    {p.priority}
                  </span>
                  <span className="text-[10.5px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">
                    {p.status}
                  </span>
                </div>
              </div>
              <button className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100">
                <MoreHorizontal size={13} />
              </button>
            </div>
            <div className="mb-3">
              <div className="flex justify-between mb-1.5">
                <span className="text-[11px] text-gray-500">
                  Прогресс
                </span>
                <span
                  className="text-[11px] font-semibold"
                  style={{ color: p.color }}
                >
                  {p.progress}%
                </span>
              </div>
              <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${p.progress}%`,
                    background: p.color,
                  }}
                />
              </div>
            </div>
            <div className="flex items-center justify-between">
              <div className="flex -space-x-1.5">
                {p.members.map((m, i) => (
                  <div
                    key={i}
                    className="w-7 h-7 rounded-full border-2 border-white flex items-center justify-center text-white text-[9px] font-bold"
                    style={{ background: p.color }}
                  >
                    {m}
                  </div>
                ))}
              </div>
              <span className="text-[10.5px] text-gray-400">
                ⏰ {p.deadline}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Tasks Tab ────────────────────────────────────────────────────────────────
interface TaskItem {
  id: number;
  text: string;
  time?: string;
  done: boolean;
  priority: "high" | "mid" | "low";
  project?: string;
}
const TASKS_DATA: TaskItem[] = [
  {
    id: 1,
    text: "Подготовить презентацию для клиента",
    time: "11:00",
    done: false,
    priority: "high",
    project: "Редизайн сайта",
  },
  {
    id: 2,
    text: "Проверить финансовый отчёт",
    time: "14:00",
    done: false,
    priority: "high",
    project: "Финансовый отчёт Q1",
  },
  {
    id: 3,
    text: "Написать статью в базу знаний",
    time: "16:00",
    done: true,
    priority: "mid",
  },
  {
    id: 4,
    text: "Позвонить Марине по проекту",
    time: "18:00",
    done: false,
    priority: "mid",
    project: "Редизайн сайта",
  },
  {
    id: 5,
    text: "Настроить автоматизацию задач",
    done: false,
    priority: "low",
  },
  {
    id: 6,
    text: "Обновить профиль в Stoic",
    done: true,
    priority: "low",
  },
];
const TASK_TABS = [
  "Все",
  "Сегодня",
  "Предстоящие",
  "Завершённые",
];
const PRIORITY_MAP: Record<
  string,
  { label: string; color: string }
> = {
  high: { label: "Высокий", color: "#E17055" },
  mid: { label: "Средний", color: "#FD9644" },
  low: { label: "Низкий", color: "#00B894" },
};

function TasksTab() {
  const [activeFilter, setActiveFilter] = useState("Все");
  const [tasks, setTasks] = useState<TaskItem[]>(TASKS_DATA);
  const [newTask, setNewTask] = useState("");

  const filtered = tasks.filter((t) => {
    if (activeFilter === "Завершённые") return t.done;
    if (activeFilter === "Сегодня") return !t.done && !!t.time;
    if (activeFilter === "Предстоящие")
      return !t.done && !t.time;
    return true;
  });

  const toggle = (id: number) =>
    setTasks((ts) =>
      ts.map((t) =>
        t.id === id ? { ...t, done: !t.done } : t,
      ),
    );

  const addTask = () => {
    if (!newTask.trim()) return;
    setTasks((ts) => [
      ...ts,
      {
        id: Date.now(),
        text: newTask.trim(),
        done: false,
        priority: "mid",
      },
    ]);
    setNewTask("");
  };

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex gap-1.5">
          {TASK_TABS.map((t) => (
            <button
              key={t}
              onClick={() => setActiveFilter(t)}
              className={`px-3 py-1.5 rounded-full text-[11.5px] transition-colors ${activeFilter === t ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5 text-[11.5px] text-gray-500">
          <CheckSquare size={13} className="text-[#00B894]" />
          {tasks.filter((t) => t.done).length} из {tasks.length}{" "}
          выполнено
        </div>
      </div>

      {/* Add task */}
      <div className="flex items-center gap-2 bg-white rounded-2xl px-4 py-3 border border-gray-200">
        <Plus
          size={15}
          className="text-gray-400 flex-shrink-0"
        />
        <input
          value={newTask}
          onChange={(e) => setNewTask(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addTask()}
          placeholder="Добавить новую задачу..."
          className="flex-1 text-[13px] outline-none placeholder:text-gray-400 text-gray-700 bg-transparent"
        />
        <button
          onClick={addTask}
          className="px-3 py-1 rounded-lg bg-[#EEF3FE] text-[#4B78F5] text-[12px] font-medium hover:bg-blue-100"
        >
          + Добавить
        </button>
      </div>

      {/* Task list */}
      <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-400">
            <CheckSquare size={32} strokeWidth={1.2} />
            <p className="mt-2 text-[13px]">
              Задачи не найдены
            </p>
          </div>
        ) : (
          filtered.map((t, i) => {
            const pr = PRIORITY_MAP[t.priority];
            return (
              <div
                key={t.id}
                className={`flex items-start gap-3 px-4 py-3.5 hover:bg-gray-50 transition-colors ${i < filtered.length - 1 ? "border-b border-gray-50" : ""}`}
              >
                <button
                  onClick={() => toggle(t.id)}
                  className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 mt-0.5 transition-all ${t.done ? "border-[#00B894] bg-[#00B894]" : "border-gray-300 hover:border-[#4B78F5]"}`}
                >
                  {t.done && (
                    <Check
                      size={10}
                      className="text-white"
                      strokeWidth={3}
                    />
                  )}
                </button>
                <div className="flex-1 min-w-0">
                  <p
                    className={`text-[13px] leading-snug ${t.done ? "text-gray-400 line-through" : "text-gray-800"}`}
                  >
                    {t.text}
                  </p>
                  {t.project && (
                    <p className="text-[11px] text-[#4B78F5] mt-0.5">
                      📁 {t.project}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded-full"
                    style={{
                      background: `${pr.color}18`,
                      color: pr.color,
                      fontWeight: 500,
                    }}
                  >
                    {pr.label}
                  </span>
                  {t.time && (
                    <span className="text-[11px] text-gray-400">
                      ⏰ {t.time}
                    </span>
                  )}
                  <button
                    onClick={() =>
                      setTasks((ts) =>
                        ts.filter((x) => x.id !== t.id),
                      )
                    }
                    className="w-6 h-6 flex items-center justify-center rounded-lg text-gray-300 hover:text-red-400 hover:bg-red-50 transition-colors"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ─── Calendar Tab ─────────────────────────────────────────────────────────────
const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

interface CalendarEvent {
  id: string;
  title: string;
  date: string;
  time: string;
  color: string;
  location?: string | null;
}

interface CalendarEventsResponse {
  items: CalendarEvent[];
}

function getBrowserTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Moscow";
}

function dateKey(year: number, month: number, day: number) {
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function CalendarTab() {
  const todayDate = new Date();
  const [month, setMonth] = useState(todayDate.getMonth());
  const [year, setYear] = useState(todayDate.getFullYear());
  const [selectedDay, setSelectedDay] = useState<number | null>(
    todayDate.getDate(),
  );
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [eventsError, setEventsError] = useState(false);

  const monthNames = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
  ];
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  // Day of week for first of month (Monday=0)
  const firstDow = (new Date(year, month, 1).getDay() + 6) % 7;
  const cells: (number | null)[] = [
    ...Array(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  const prev = () => {
    if (month === 0) {
      setMonth(11);
      setYear((y) => y - 1);
    } else setMonth((m) => m - 1);
  };
  const next = () => {
    if (month === 11) {
      setMonth(0);
      setYear((y) => y + 1);
    } else setMonth((m) => m + 1);
  };
  const isToday = (d: number) =>
    month === todayDate.getMonth() && year === todayDate.getFullYear() && d === todayDate.getDate();

  useEffect(() => {
    const controller = new AbortController();
    const dateFrom = dateKey(year, month, 1);
    const dateTo = dateKey(year, month, new Date(year, month + 1, 0).getDate());
    const timezone = encodeURIComponent(getBrowserTimeZone());

    async function loadCalendarEvents() {
      setEventsError(false);
      try {
        const response = await apiClient(
          `/api/v1/calendar/events?date_from=${dateFrom}&date_to=${dateTo}&timezone=${timezone}`,
          { signal: controller.signal },
        );

        if (!response.ok) {
          throw new Error(`Calendar request failed: ${response.status}`);
        }

        const data = (await response.json()) as CalendarEventsResponse;
        setEvents(data.items);
      } catch (error) {
        if (!controller.signal.aborted) {
          setEvents([]);
          setEventsError(true);
        }
      }
    }

    loadCalendarEvents();
    return () => controller.abort();
  }, [month, year]);

  const eventsByDay = events.reduce<Record<number, CalendarEvent[]>>((acc, event) => {
    const day = new Date(`${event.date}T00:00:00`).getDate();
    acc[day] = [...(acc[day] ?? []), event];
    return acc;
  }, {});

  const selectedEvents = selectedDay
    ? (eventsByDay[selectedDay] ?? [])
    : [];
  const upcomingEvents = events
    .filter((event) => {
      if (!selectedDay) return true;
      return new Date(`${event.date}T00:00:00`).getDate() >= selectedDay;
    })
    .slice(0, 3);

  return (
    <div className="p-5">
      <div className="flex gap-4 flex-col xl:flex-row">
        {/* Calendar grid */}
        <div
          className="flex-1 bg-white rounded-2xl border border-gray-100 overflow-hidden"
          style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.06)" }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-50">
            <button
              onClick={prev}
              className="w-8 h-8 flex items-center justify-center rounded-xl hover:bg-gray-100 text-gray-500 transition-colors"
            >
              <ChevronLeft size={16} />
            </button>
            <h3 className="text-[14px] text-gray-800 font-semibold">
              {monthNames[month]} {year}
            </h3>
            <button
              onClick={next}
              className="w-8 h-8 flex items-center justify-center rounded-xl hover:bg-gray-100 text-gray-500 transition-colors"
            >
              <ChevronRight size={16} />
            </button>
          </div>
          {/* Weekday headers */}
          <div className="grid grid-cols-7 px-4 pt-3 pb-1">
            {WEEKDAYS.map((d) => (
              <div
                key={d}
                className="text-center text-[11px] text-gray-400 font-semibold py-1"
              >
                {d}
              </div>
            ))}
          </div>
          {/* Day cells */}
          <div className="grid grid-cols-7 px-3 pb-4 gap-0.5">
            {cells.map((d, i) => {
              const dayEvents = d ? eventsByDay[d] : undefined;
              const today = d ? isToday(d) : false;
              const selected = d === selectedDay;
              return (
                <button
                  key={i}
                  onClick={() => d && setSelectedDay(d)}
                  disabled={!d}
                  className={`relative flex flex-col items-center pt-1.5 pb-1 rounded-xl min-h-[52px] transition-all
                    ${!d ? "pointer-events-none" : "hover:bg-gray-50"}
                    ${selected && !today ? "bg-[#EEF3FE]" : ""}
                    ${today ? "!bg-[#4B78F5]" : ""}
                  `}
                >
                  {d && (
                    <span
                      className={`text-[12.5px] font-medium ${today ? "text-white" : selected ? "text-[#4B78F5]" : "text-gray-700"}`}
                    >
                      {d}
                    </span>
                  )}
                  {dayEvents && (
                    <div className="flex gap-0.5 mt-0.5 flex-wrap justify-center px-1">
                      {dayEvents
                        .slice(0, 2)
                        .map((ev, ei) => (
                          <div
                            key={ei}
                            className="w-1.5 h-1.5 rounded-full"
                            style={{
                              background: today
                                ? "white"
                                : ev.color,
                            }}
                          />
                        ))}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Event sidebar */}
        <div
          className="xl:w-72 bg-white rounded-2xl border border-gray-100 overflow-hidden"
          style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.06)" }}
        >
          <div className="flex items-center justify-between px-4 py-3.5 border-b border-gray-50">
            <p className="text-[13.5px] text-gray-800 font-semibold">
              {selectedDay
                ? `${selectedDay} ${monthNames[month]}`
                : "Выберите дату"}
            </p>
            <button
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11.5px] text-white font-medium"
              style={{ background: "#4B78F5" }}
            >
              <Plus size={12} />
              Событие
            </button>
          </div>
          <div className="p-3 space-y-2">
            {eventsError ? (
              <div className="flex flex-col items-center justify-center py-8 text-gray-400">
                <Calendar size={28} strokeWidth={1.2} />
                <p className="text-[12px] mt-2">Календарь API недоступен</p>
              </div>
            ) : selectedEvents.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-gray-400">
                <Calendar size={28} strokeWidth={1.2} />
                <p className="text-[12px] mt-2">Нет событий</p>
              </div>
            ) : (
              selectedEvents.map((ev, i) => (
                <div
                  key={i}
                  className="flex items-start gap-3 p-3 rounded-xl hover:bg-gray-50 transition-colors cursor-pointer"
                >
                  <div
                    className="w-1 self-stretch rounded-full flex-shrink-0"
                    style={{ background: ev.color }}
                  />
                  <div>
                    <p className="text-[12.5px] text-gray-800 font-medium">
                      {ev.title}
                    </p>
                    {ev.time && (
                      <p className="text-[11px] text-gray-400 mt-0.5">
                        ⏰ {ev.time}
                      </p>
                    )}
                    {ev.location && (
                      <p className="text-[10.5px] text-gray-400 mt-0.5">
                        {ev.location}
                      </p>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
          {/* Upcoming */}
          <div className="px-4 pb-4">
            <p className="text-[11px] text-gray-400 font-semibold mb-2">
              Предстоящие
            </p>
            <div className="space-y-2">
              {upcomingEvents.length === 0 && (
                <p className="text-[12px] text-gray-400">Нет ближайших событий</p>
              )}
              {upcomingEvents.map((e) => (
                <div
                  key={e.id}
                  className="flex items-center gap-2.5 p-2 rounded-lg hover:bg-gray-50 cursor-pointer"
                >
                  <div
                    className="w-8 h-8 rounded-lg flex items-center justify-center text-[12px] font-bold text-white flex-shrink-0"
                    style={{ background: e.color }}
                  >
                    {new Date(`${e.date}T00:00:00`).getDate()}
                  </div>
                  <div>
                    <p className="text-[12px] text-gray-700">{e.title}</p>
                    <p className="text-[10.5px] text-gray-400">{e.time}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Notes Tab ────────────────────────────────────────────────────────────────
interface Note {
  id: number;
  title: string;
  preview: string;
  date: string;
  tags: string[];
  color: string;
  icon: string;
  pinned?: boolean;
}
const NOTES_DATA: Note[] = [
  {
    id: 1,
    title: "Идеи для блога на июнь",
    preview:
      "1. Продуктивность в Stoic\n2. Как я веду заметки\n3. GTD методология",
    date: "14 апр",
    tags: ["Блог", "Идеи"],
    color: "#4B78F5",
    icon: "📝",
  },
  {
    id: 2,
    title: "Чек-лист запуска продукта",
    preview:
      "— Финальное тестирование\n— Публикация лендинга\n— Рассылка анонса",
    date: "13 апр",
    tags: ["Проекты"],
    color: "#00B894",
    icon: "✅",
    pinned: true,
  },
  {
    id: 3,
    title: "Конспект: Думай и богатей",
    preview:
      "Ключевая мысль: желание + вера + настойчивость = успех. Важна визуализация целей.",
    date: "12 апр",
    tags: ["Книги"],
    color: "#6C5CE7",
    icon: "📚",
  },
  {
    id: 4,
    title: "Стратегия 2026",
    preview:
      "Фокус на 3 ключевых направлениях: продукт, команда, масштабирование.",
    date: "10 апр",
    tags: ["Стратегия"],
    color: "#FD9644",
    icon: "🎯",
    pinned: true,
  },
  {
    id: 5,
    title: "Встреча с Алексеем — итоги",
    preview:
      "Договорились о дизайне, обсудили сроки. Следующий шаг — прототип.",
    date: "8 апр",
    tags: ["Встречи"],
    color: "#E17055",
    icon: "🤝",
  },
  {
    id: 6,
    title: "Полезные ресурсы по UX",
    preview:
      "Nielsen Norman Group, Smashing Magazine, Figma Community",
    date: "5 апр",
    tags: ["Обучение", "UX"],
    color: "#A29BFE",
    icon: "🔗",
  },
];
const NOTE_TAGS = [
  "Все",
  "Блог",
  "Книги",
  "Проекты",
  "Стратегия",
  "Обучение",
];

function NotesTab() {
  const [filter, setFilter] = useState("Все");
  const [search, setSearch] = useState("");
  const filtered = NOTES_DATA.filter((n) => {
    if (filter !== "Все" && !n.tags.includes(filter))
      return false;
    if (
      search &&
      !n.title.toLowerCase().includes(search.toLowerCase())
    )
      return false;
    return true;
  });
  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative">
            <Search
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"
            />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Поиск заметок..."
              className="pl-8 pr-3 py-2 text-[12.5px] bg-white border border-gray-200 rounded-xl outline-none w-52"
            />
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {NOTE_TAGS.map((t) => (
              <button
                key={t}
                onClick={() => setFilter(t)}
                className={`px-2.5 py-1 rounded-full text-[11px] transition-colors ${filter === t ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <button
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-white text-[12.5px] font-medium"
          style={{
            background:
              "linear-gradient(135deg, #4B78F5, #6C5CE7)",
          }}
        >
          <Plus size={13} />
          Новая заметка
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map((n) => (
          <div
            key={n.id}
            className="bg-white rounded-2xl p-4 border border-gray-100 hover:border-blue-100 hover:shadow-md transition-all cursor-pointer"
          >
            <div className="flex items-start justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="text-xl">{n.icon}</span>
                {n.pinned && (
                  <Star
                    size={12}
                    className="text-[#FD9644]"
                    fill="#FD9644"
                  />
                )}
              </div>
              <button className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-300 hover:bg-gray-100 hover:text-gray-500">
                <MoreHorizontal size={13} />
              </button>
            </div>
            <h4 className="text-[13.5px] text-gray-800 font-semibold mb-1.5">
              {n.title}
            </h4>
            <p className="text-[12px] text-gray-500 leading-relaxed whitespace-pre-line line-clamp-3">
              {n.preview}
            </p>
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-50">
              <div className="flex gap-1 flex-wrap">
                {n.tags.map((tag) => (
                  <span
                    key={tag}
                    className="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
                    style={{
                      background: `${n.color}18`,
                      color: n.color,
                    }}
                  >
                    {tag}
                  </span>
                ))}
              </div>
              <span className="text-[10.5px] text-gray-400 flex-shrink-0 ml-2">
                {n.date}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main KnowledgePage ───────────────────────────────────────────────────────
export function KnowledgePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") ?? "files";

  const setTab = (id: string) => setSearchParams({ tab: id });

  const renderTab = () => {
    switch (activeTab) {
      case "files":
        return <FilesTab />;
      case "contacts":
        return <ContactsTab />;
      case "projects":
        return <ProjectsTab />;
      case "tasks":
        return <TasksTab />;
      case "calendar":
        return <CalendarTab />;
      case "notes":
        return <NotesTab />;
      default:
        return <FilesTab />;
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#F6F7FB]">
      {/* Tab bar */}
      <div className="bg-white border-b border-gray-100 px-4 sm:px-6 flex-shrink-0">
        <div className="flex gap-0 overflow-x-auto hide-scrollbar">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-3.5 text-[13px] whitespace-nowrap border-b-2 transition-colors flex-shrink-0
                  ${active ? "text-[#4B78F5] border-[#4B78F5] font-medium" : "text-gray-500 border-transparent hover:text-gray-700"}`}
              >
                <Icon
                  size={15}
                  className={
                    active ? "text-[#4B78F5]" : "text-gray-400"
                  }
                />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {renderTab()}
      </div>

      <style>{`
        .hide-scrollbar::-webkit-scrollbar { display: none; }
        .hide-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
      `}</style>
    </div>
  );
}

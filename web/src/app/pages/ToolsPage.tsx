import { useState } from "react";
import { useSearchParams } from "react-router";
import {
  Cpu, Puzzle, LayoutTemplate, Plus, ToggleLeft, ToggleRight,
  Zap, Clock, CheckSquare, MessageSquare, Calendar, FileText,
  ChevronRight, ExternalLink, Search, Star, Download, MoreHorizontal,
} from "lucide-react";

const TABS = [
  { id: "automation", label: "Автоматизация", icon: Cpu },
  { id: "integrations", label: "Интеграции", icon: Puzzle },
  { id: "templates", label: "Шаблоны", icon: LayoutTemplate },
];

// ─── Automation Tab ───────────────────────────────────────────────────────────
interface AutoRule { id: number; name: string; trigger: string; action: string; icon: React.ComponentType<{ size?: number; className?: string }>; active: boolean; runs: number; color: string; }
const AUTO_RULES: AutoRule[] = [
  { id: 1, name: "Ежедневный обзор", trigger: "Каждый день в 09:00", action: "Создать задачи дня из шаблона", icon: Clock, active: true, runs: 34, color: "#4B78F5" },
  { id: 2, name: "Завершение задачи", trigger: "При завершении задачи", action: "Уведомить команду в Telegram", icon: CheckSquare, active: true, runs: 87, color: "#00B894" },
  { id: 3, name: "Новое сообщение", trigger: "Новое сообщение в Telegram", action: "Создать задачу из сообщения", icon: MessageSquare, active: false, runs: 12, color: "#6C5CE7" },
  { id: 4, name: "Дедлайн проекта", trigger: "За 2 дня до дедлайна", action: "Напомнить об окончании проекта", icon: Calendar, active: true, runs: 5, color: "#FD9644" },
  { id: 5, name: "Сохранение ссылки", trigger: "Новая закладка сохранена", action: "Добавить тег и категорию", icon: FileText, active: false, runs: 23, color: "#E17055" },
];

function AutomationTab() {
  const [rules, setRules] = useState<AutoRule[]>(AUTO_RULES);
  const toggle = (id: number) => setRules((rs) => rs.map((r) => r.id === id ? { ...r, active: !r.active } : r));

  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-[14px] text-gray-800 font-semibold">Правила автоматизации</h3>
          <p className="text-[12px] text-gray-400 mt-0.5">{rules.filter((r) => r.active).length} из {rules.length} активно</p>
        </div>
        <button className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-white text-[12.5px] font-medium" style={{ background: "linear-gradient(135deg, #4B78F5, #6C5CE7)" }}>
          <Plus size={13} />Новое правило
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3">
        {[
          { label: "Всего запусков", value: "161", color: "#4B78F5", bg: "#EEF3FE" },
          { label: "Активных правил", value: rules.filter((r) => r.active).length.toString(), color: "#00B894", bg: "#E6FAF5" },
          { label: "Сэкономлено времени", value: "4.2 ч", color: "#6C5CE7", bg: "#F0EEFF" },
        ].map((s) => (
          <div key={s.label} className="bg-white rounded-2xl px-4 py-3.5 border border-gray-100" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
            <p className="text-[11px] text-gray-400">{s.label}</p>
            <p className="text-[22px] font-bold mt-0.5" style={{ color: s.color }}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Rules */}
      <div className="space-y-3">
        {rules.map((r) => {
          const Icon = r.icon;
          return (
            <div key={r.id} className="bg-white rounded-2xl px-4 py-4 border border-gray-100 hover:border-blue-100 transition-all" style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ background: `${r.color}18` }}>
                  <Icon size={18} style={{ color: r.color }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-[13.5px] text-gray-800 font-semibold">{r.name}</p>
                    {r.active && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 font-medium">Активно</span>}
                  </div>
                  <div className="flex items-center gap-2 mt-1.5 text-[12px] text-gray-500">
                    <span className="flex items-center gap-1"><Zap size={11} className="text-[#FD9644]" />{r.trigger}</span>
                    <ChevronRight size={12} className="text-gray-300 flex-shrink-0" />
                    <span>{r.action}</span>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-1">Запусков: {r.runs}</p>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button onClick={() => toggle(r.id)} className="text-gray-400 hover:text-[#4B78F5] transition-colors">
                    {r.active ? <ToggleRight size={26} className="text-[#4B78F5]" /> : <ToggleLeft size={26} />}
                  </button>
                  <button className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-300 hover:bg-gray-100 hover:text-gray-600"><MoreHorizontal size={14} /></button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Integrations Tab ─────────────────────────────────────────────────────────
interface Integration { id: number; name: string; desc: string; icon: string; connected: boolean; color: string; category: string; }
const INTEGRATIONS: Integration[] = [
  { id: 1, name: "Telegram", desc: "Синхронизация сообщений", icon: "✈️", connected: true, color: "#0088CC", category: "Мессенджеры" },
  { id: 2, name: "WhatsApp", desc: "Управление диалогами", icon: "💬", connected: true, color: "#25D366", category: "Мессенджеры" },
  { id: 3, name: "Gmail", desc: "Email и рассылки", icon: "📧", connected: false, color: "#EA4335", category: "Email" },
  { id: 4, name: "Google Calendar", desc: "Синхронизация событий", icon: "📅", connected: true, color: "#4285F4", category: "Продуктивность" },
  { id: 5, name: "Notion", desc: "Импорт базы знаний", icon: "📋", connected: false, color: "#000000", category: "Знания" },
  { id: 6, name: "GitHub", desc: "Отслеживание задач разработки", icon: "🐙", connected: false, color: "#333333", category: "Разработка" },
  { id: 7, name: "Slack", desc: "Командные уведомления", icon: "🔔", connected: false, color: "#4A154B", category: "Мессенджеры" },
  { id: 8, name: "Figma", desc: "Дизайн-файлы и макеты", icon: "🎨", connected: true, color: "#F24E1E", category: "Дизайн" },
  { id: 9, name: "Zapier", desc: "Автоматизация процессов", icon: "⚡", connected: false, color: "#FF4A00", category: "Автоматизация" },
];
const INT_CATEGORIES = ["Все", "Мессенджеры", "Email", "Продуктивность", "Знания", "Дизайн", "Разработка"];

function IntegrationsTab() {
  const [integrations, setIntegrations] = useState<Integration[]>(INTEGRATIONS);
  const [cat, setCat] = useState("Все");
  const toggle = (id: number) => setIntegrations((is) => is.map((i) => i.id === id ? { ...i, connected: !i.connected } : i));
  const filtered = cat === "Все" ? integrations : integrations.filter((i) => i.category === cat);

  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-[14px] text-gray-800 font-semibold">Интеграции</h3>
          <p className="text-[12px] text-gray-400 mt-0.5">{integrations.filter((i) => i.connected).length} подключено</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative"><Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" /><input placeholder="Найти интеграцию..." className="pl-8 pr-3 py-2 text-[12px] bg-white border border-gray-200 rounded-xl outline-none w-44" /></div>
        </div>
      </div>

      <div className="flex gap-1.5 flex-wrap">
        {INT_CATEGORIES.map((c) => <button key={c} onClick={() => setCat(c)} className={`px-3 py-1.5 rounded-full text-[11.5px] transition-colors ${cat === c ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}>{c}</button>)}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
        {filtered.map((int) => (
          <div key={int.id} className={`bg-white rounded-2xl p-4 border transition-all ${int.connected ? "border-blue-100" : "border-gray-100 hover:border-gray-200"}`} style={{ boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
            <div className="flex items-start gap-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 text-xl" style={{ background: `${int.color}14` }}>{int.icon}</div>
              <div className="flex-1 min-w-0">
                <p className="text-[13.5px] text-gray-800 font-semibold">{int.name}</p>
                <p className="text-[11.5px] text-gray-400 mt-0.5">{int.desc}</p>
                <p className="text-[10.5px] text-gray-300 mt-0.5">{int.category}</p>
              </div>
              {int.connected && <span className="w-2 h-2 rounded-full bg-emerald-400 flex-shrink-0 mt-1.5" />}
            </div>
            <div className="mt-3.5 flex items-center gap-2">
              <button
                onClick={() => toggle(int.id)}
                className={`flex-1 py-1.5 rounded-xl text-[12px] font-medium transition-colors ${int.connected ? "bg-red-50 text-red-500 hover:bg-red-100" : "text-white"}`}
                style={int.connected ? {} : { background: `linear-gradient(135deg, #4B78F5, #6C5CE7)` }}
              >
                {int.connected ? "Отключить" : "Подключить"}
              </button>
              {int.connected && <button className="w-8 h-8 flex items-center justify-center rounded-xl border border-gray-100 text-gray-400 hover:bg-gray-50"><ExternalLink size={13} /></button>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Templates Tab ────────────────────────────────────────────────────────────
const TEMPLATES = [
  { id: 1, name: "Ежедневные задачи", desc: "Стандартный список зад��ч на день с приоритетами и временными слотами.", icon: "✅", category: "Задачи", uses: 124, color: "#4B78F5", rating: 4.8 },
  { id: 2, name: "Встреча 1-на-1", desc: "Шаблон для проведения регулярных встреч с сотрудниками.", icon: "🤝", category: "Встречи", uses: 89, color: "#00B894", rating: 4.9 },
  { id: 3, name: "Запуск проекта", desc: "Чек-лист и план для запуска нового проекта от идеи до релиза.", icon: "🚀", category: "Проекты", uses: 67, color: "#6C5CE7", rating: 4.7 },
  { id: 4, name: "Обзор недели", desc: "Структурированный шаблон для ежен недельного обзора задач и целей.", icon: "📊", category: "Продуктивность", uses: 203, color: "#FD9644", rating: 5.0 },
  { id: 5, name: "Конспект книги", desc: "Шаблон для структурированного конспектирования книг.", icon: "📚", category: "Знания", uses: 156, color: "#E17055", rating: 4.6 },
  { id: 6, name: "Бриф для клиента", desc: "Профессиональный бриф для сбора требований от клиента.", icon: "📋", category: "Клиенты", uses: 45, color: "#A29BFE", rating: 4.5 },
];
const TEMPL_CATS = ["Все", "Задачи", "Встречи", "Проекты", "Продуктивность", "Знания", "Клиенты"];

function TemplatesTab() {
  const [cat, setCat] = useState("Все");
  const filtered = cat === "Все" ? TEMPLATES : TEMPLATES.filter((t) => t.category === cat);
  return (
    <div className="p-5 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h3 className="text-[14px] text-gray-800 font-semibold">Библиотека шаблонов</h3>
          <p className="text-[12px] text-gray-400 mt-0.5">{TEMPLATES.length} шаблонов доступно</p>
        </div>
        <button className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-white text-[12.5px] font-medium" style={{ background: "linear-gradient(135deg, #4B78F5, #6C5CE7)" }}>
          <Plus size={13} />Создать шаблон
        </button>
      </div>
      <div className="flex gap-1.5 flex-wrap">
        {TEMPL_CATS.map((c) => <button key={c} onClick={() => setCat(c)} className={`px-3 py-1.5 rounded-full text-[11.5px] transition-colors ${cat === c ? "bg-[#4B78F5] text-white font-medium" : "bg-gray-100 text-gray-500 hover:bg-gray-200"}`}>{c}</button>)}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map((t) => (
          <div key={t.id} className="bg-white rounded-2xl p-4 border border-gray-100 hover:border-blue-100 hover:shadow-md transition-all">
            <div className="flex items-start gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center text-xl flex-shrink-0" style={{ background: `${t.color}14` }}>{t.icon}</div>
              <div className="flex-1 min-w-0">
                <p className="text-[13.5px] text-gray-800 font-semibold">{t.name}</p>
                <span className="text-[10px] px-2 py-0.5 rounded-full font-medium" style={{ background: `${t.color}18`, color: t.color }}>{t.category}</span>
              </div>
            </div>
            <p className="text-[12px] text-gray-500 leading-relaxed">{t.desc}</p>
            <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-50">
              <div className="flex items-center gap-3 text-[11px] text-gray-400">
                <span className="flex items-center gap-1"><Star size={11} className="text-[#FD9644]" fill="#FD9644" /> {t.rating}</span>
                <span><Download size={11} className="inline mr-1" />{t.uses}</span>
              </div>
              <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[12px] font-medium text-[#4B78F5] bg-[#EEF3FE] hover:bg-blue-100 transition-colors">
                Использовать
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export function ToolsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const active = searchParams.get("tab") ?? "automation";
  const setTab = (id: string) => setSearchParams({ tab: id });

  const render = () => {
    switch (active) {
      case "automation": return <AutomationTab />;
      case "integrations": return <IntegrationsTab />;
      case "templates": return <TemplatesTab />;
      default: return <AutomationTab />;
    }
  };

  return (
    <div className="flex flex-col h-full bg-[#F6F7FB]">
      <div className="bg-white border-b border-gray-100 px-4 sm:px-6 flex-shrink-0">
        <div className="flex gap-0 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = active === tab.id;
            return (
              <button key={tab.id} onClick={() => setTab(tab.id)}
                className={`flex items-center gap-2 px-5 py-3.5 text-[13px] whitespace-nowrap border-b-2 transition-colors flex-shrink-0
                  ${isActive ? "text-[#4B78F5] border-[#4B78F5] font-medium" : "text-gray-500 border-transparent hover:text-gray-700"}`}>
                <Icon size={15} className={isActive ? "text-[#4B78F5]" : "text-gray-400"} />
                {tab.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="flex-1 overflow-y-auto">{render()}</div>
    </div>
  );
}

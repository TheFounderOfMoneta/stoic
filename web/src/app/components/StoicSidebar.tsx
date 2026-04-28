import { useState, useRef } from "react";
import { createPortal } from "react-dom";
import { useNavigate, useLocation } from "react-router";
import stoicLogo from "@/assets/stoic-logo.png";
import {
  Home, Bot, MessageSquare, BookOpen, Wrench, Settings,
  ChevronRight, FileText, Users, FolderOpen, CheckSquare,
  Calendar, StickyNote, Cpu, Puzzle, LayoutTemplate, User,
  HardDrive, Sliders, X,
  ChevronRight as ChevronRightIcon,
} from "lucide-react";

interface SubItem {
  icon: React.ComponentType<{ size?: number; className?: string; strokeWidth?: number }>;
  label: string;
  tab: string;
}

interface NavItem {
  id: string;
  href: string;
  icon: React.ComponentType<{ size?: number; className?: string; strokeWidth?: number }>;
  label: string;
  submenu?: SubItem[];
}

const NAV_ITEMS: NavItem[] = [
  { id: "home", href: "/home", icon: Home, label: "Главная" },
  { id: "assistant", href: "/assistant", icon: Bot, label: "Ассистент" },
  { id: "messages", href: "/messages", icon: MessageSquare, label: "Сообщения" },
  {
    id: "knowledge", href: "/knowledge", icon: BookOpen, label: "База знаний",
    submenu: [
      { icon: FileText, label: "Файлы", tab: "files" },
      { icon: Users, label: "Контакты", tab: "contacts" },
      { icon: FolderOpen, label: "Проекты", tab: "projects" },
      { icon: CheckSquare, label: "Задачи", tab: "tasks" },
      { icon: Calendar, label: "Календарь", tab: "calendar" },
      { icon: StickyNote, label: "Заметки", tab: "notes" },
    ],
  },
  {
    id: "tools", href: "/tools", icon: Wrench, label: "Инструменты",
    submenu: [
      { icon: Cpu, label: "Автоматизация", tab: "automation" },
      { icon: Puzzle, label: "Интеграции", tab: "integrations" },
      { icon: LayoutTemplate, label: "Шаблоны", tab: "templates" },
    ],
  },
  {
    id: "settings", href: "/settings", icon: Settings, label: "Настройки",
    submenu: [
      { icon: User, label: "Профиль", tab: "profile" },
      { icon: HardDrive, label: "Хранилище", tab: "storage" },
      { icon: Sliders, label: "Предпочтения", tab: "preferences" },
    ],
  },
];

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export function StoicSidebar({ collapsed, mobileOpen, onMobileClose }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [submenuTop, setSubmenuTop] = useState(0);
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const sidebarWidth = collapsed ? 68 : 230;

  const isActive = (item: NavItem) =>
    item.href === "/home"
      ? location.pathname === "/home"
      : location.pathname.startsWith(item.href);

  const handleNav = (href: string, tab?: string) => {
    const url = tab ? `${href}?tab=${tab}` : href;
    navigate(url);
    onMobileClose();
    setHoveredId(null);
  };

  const handleMouseEnterItem = (id: string, e: React.MouseEvent<HTMLDivElement>) => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
    const rect = e.currentTarget.getBoundingClientRect();
    setSubmenuTop(rect.top);
    setHoveredId(id);
  };

  const handleMouseLeave = () => {
    hoverTimeoutRef.current = setTimeout(() => setHoveredId(null), 130);
  };

  const handleMouseEnterSubmenu = () => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current);
  };

  const hoveredItem = NAV_ITEMS.find((i) => i.id === hoveredId);

  // ── Fixed-position floating panel (portal) ──────────────────────────────────
  const floatingPanel =
    hoveredId && hoveredItem
      ? createPortal(
          <div
            className="fixed z-[9999]"
            style={{ top: submenuTop, left: sidebarWidth + 8, minWidth: 180 }}
            onMouseEnter={handleMouseEnterSubmenu}
            onMouseLeave={handleMouseLeave}
          >
            {hoveredItem.submenu ? (
              /* Submenu card */
              <div
                className="bg-white rounded-2xl py-2 px-2"
                style={{
                  boxShadow: "0 8px 32px rgba(0,0,0,0.13), 0 2px 8px rgba(0,0,0,0.07)",
                  border: "1px solid rgba(0,0,0,0.07)",
                }}
              >
                <p className="text-[10.5px] text-gray-400 px-3 py-1.5 font-semibold uppercase tracking-wider">
                  {hoveredItem.label}
                </p>
                {hoveredItem.submenu.map((sub) => {
                  const SubIcon = sub.icon;
                  return (
                    <button
                      key={sub.tab}
                      onClick={() => handleNav(hoveredItem.href, sub.tab)}
                      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
                    >
                      <SubIcon size={15} className="text-gray-400" strokeWidth={1.8} />
                      <span className="text-[13px]">{sub.label}</span>
                    </button>
                  );
                })}
              </div>
            ) : collapsed ? (
              /* Tooltip for icon-only items */
              <div
                className="bg-gray-900 text-white text-[12px] px-2.5 py-1.5 rounded-lg whitespace-nowrap pointer-events-none"
                style={{ boxShadow: "0 4px 12px rgba(0,0,0,0.2)" }}
              >
                {hoveredItem.label}
              </div>
            ) : null}
          </div>,
          document.body
        )
      : null;

  // ── Desktop sidebar content ──────────────────────────────────────────────────
  const sidebarContent = (
    <div className="flex flex-col h-full bg-white border-r border-gray-100">
      {/* Logo */}
      <div
        className={`flex items-center gap-2.5 border-b border-gray-100 ${
          collapsed ? "px-4 py-5 justify-center" : "px-6 py-5"
        }`}
      >
        <div className="w-20 h-20 flex items-center justify-center flex-shrink-0">
          <img src={stoicLogo} alt="Stoic" className="w-16 h-16 object-contain" />
        </div>
        {!collapsed && (
          <span className="text-[21px] text-[#142341] font-semibold whitespace-nowrap">
            Stoic
          </span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 pt-4 px-3 space-y-1.5">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = isActive(item);
          return (
            <div
              key={item.id}
              className="relative"
              onMouseEnter={(e) => handleMouseEnterItem(item.id, e)}
              onMouseLeave={handleMouseLeave}
            >
              <button
                onClick={() => handleNav(item.href)}
                title={collapsed ? item.label : undefined}
                className={`w-full flex items-center gap-3 rounded-xl transition-all duration-150 text-sm
                  ${collapsed ? "px-0 py-3 justify-center" : "px-3.5 py-3"}
                  ${
                    active
                      ? "bg-[#EEF3FE] text-[#2F73FF]"
                      : "text-[#4F628C] hover:bg-gray-50 hover:text-[#142341]"
                  }`}
              >
                <Icon
                  size={19}
                  className={active ? "text-[#2F73FF] flex-shrink-0" : "text-[#7284AF] flex-shrink-0"}
                  strokeWidth={active ? 2.2 : 1.8}
                />
                {!collapsed && (
                  <>
                    <span className="flex-1 text-left text-[15px] font-semibold">{item.label}</span>
                    {item.submenu && (
                      <ChevronRight
                        size={14}
                        className={active ? "text-[#2F73FF]" : "text-[#9AA8C5]"}
                        strokeWidth={2}
                      />
                    )}
                  </>
                )}
              </button>
            </div>
          );
        })}
      </nav>

    </div>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <div
        className="hidden lg:flex flex-col flex-shrink-0 transition-all duration-300"
        style={{ width: collapsed ? 68 : 230 }}
      >
        {sidebarContent}
      </div>

      {/* Floating portal panel */}
      {floatingPanel}

      {/* Mobile sidebar */}
      <div
        className={`fixed top-0 left-0 h-full z-50 lg:hidden transition-transform duration-300 ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{ width: 240 }}
      >
        <div className="flex flex-col h-full bg-white shadow-2xl" style={{ width: 240 }}>
          <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl flex items-center justify-center bg-[#EEF3FE]">
              <img src={stoicLogo} alt="Stoic" className="w-5 h-5 object-contain" />
            </div>
            <span className="text-[15px] text-gray-900 font-semibold">Stoic</span>
          </div>
          <button
            onClick={onMobileClose}
              className="w-8 h-8 flex items-center justify-center rounded-xl text-gray-400 hover:bg-gray-100"
            >
              <X size={16} />
            </button>
          </div>
          <nav className="flex-1 pt-3 px-2.5 space-y-0.5 overflow-y-auto">
            {NAV_ITEMS.map((item) => {
              const Icon = item.icon;
              const active = isActive(item);
              return (
                <div key={item.id}>
                  <button
                    onClick={() => handleNav(item.href)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors text-[13.5px]
                      ${active ? "bg-[#EEF3FE] text-[#4B78F5]" : "text-gray-500 hover:bg-gray-50 hover:text-gray-800"}`}
                  >
                    <Icon
                      size={18}
                      className={active ? "text-[#4B78F5]" : "text-gray-400"}
                      strokeWidth={active ? 2.2 : 1.8}
                    />
                    <span className="flex-1 text-left font-medium">{item.label}</span>
                  </button>
                  {item.submenu && active && (
                    <div className="ml-4 mt-0.5 space-y-0.5 border-l-2 border-[#EEF3FE] pl-2.5 mb-1">
                      {item.submenu.map((sub) => {
                        const SubIcon = sub.icon;
                        return (
                          <button
                            key={sub.tab}
                            onClick={() => handleNav(item.href, sub.tab)}
                            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12.5px] text-gray-500 hover:bg-gray-50 hover:text-gray-800 transition-colors"
                          >
                            <SubIcon size={14} className="text-gray-400" strokeWidth={1.8} />
                            {sub.label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </nav>
        </div>
      </div>
    </>
  );
}

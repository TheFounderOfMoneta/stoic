import { useState } from "react";
import { Outlet } from "react-router";
import { StoicSidebar } from "./StoicSidebar";
import { StoicTopBar } from "./StoicTopBar";

export function Layout() {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex w-full bg-[#F6F7FB] overflow-hidden" style={{ height: "100dvh", minHeight: 0 }}>
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <StoicSidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onMobileClose={() => setMobileOpen(false)}
      />

      {/* Main */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <StoicTopBar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((c) => !c)}
          onMobileMenu={() => setMobileOpen((o) => !o)}
        />
        <main className="flex-1 overflow-y-auto overflow-x-hidden">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

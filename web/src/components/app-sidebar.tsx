'use client';

import { useApiKey } from '@/context/api-key-context';
import { cn } from '@/lib/utils';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  BarChart3,
  Database,
  GaugeCircle,
  Key,
  Menu,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sparkles,
  Terminal,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

const SIDEBAR_COLLAPSED_KEY = 'echo_gate_sidebar_collapsed';

export function AppSidebar() {
  const pathname = usePathname();
  const { apiKey } = useApiKey();

  // Desktop collapse state (w-64 vs w-16 icon rail)
  const [isCollapsed, setIsCollapsed] = useState(() => {
    if (typeof window === 'undefined') return false;
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === 'true';
    } catch {
      return false;
    }
  });
  // Mobile overlay open state
  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const [prevPathname, setPrevPathname] = useState(pathname);

  // Close mobile drawer when route changes during render
  if (prevPathname !== pathname) {
    setPrevPathname(pathname);
    setIsMobileOpen(false);
  }

  // Keyboard shortcut Ctrl+B or Cmd+B to toggle collapse
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        setIsCollapsed((prev) => {
          const next = !prev;
          try {
            localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
          } catch {
            // ignore
          }
          return next;
        });
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const toggleCollapsed = () => {
    setIsCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(next));
      } catch {
        // ignore
      }
      return next;
    });
  };

  const routes = [
    { name: 'Playground', path: '/', icon: Terminal },
    { name: 'Conversations', path: '/conversations', icon: MessagesSquare },
    { name: 'Analytics', path: '/analytics', icon: BarChart3 },
    { name: 'Traces', path: '/traces', icon: Activity },
    { name: 'Quota', path: '/quota', icon: GaugeCircle },
    { name: 'Cache Admin', path: '/admin/cache', icon: Database },
    { name: 'Cache Explorer', path: '/admin/explorer', icon: Search },
    { name: 'Settings', path: '/settings', icon: Settings },
  ];

  const sidebarContent = (collapsed: boolean) => (
    <div className="flex flex-col h-full select-none">
      {/* Header */}
      <div className="h-14 border-b border-border flex items-center px-3.5 justify-between shrink-0">
        {!collapsed ? (
          <>
            <Link
              href="/"
              className="flex items-center gap-2.5 min-w-0 group hover:opacity-90 transition-opacity"
            >
              <div className="w-8 h-8 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0 text-primary group-hover:bg-primary/15 transition-colors">
                <Sparkles className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <div className="text-sm font-bold text-foreground truncate leading-tight tracking-tight">
                  Echo Gate
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="text-[10px] text-muted-foreground font-mono">
                    Gateway Live
                  </span>
                </div>
              </div>
            </Link>
            <button
              onClick={toggleCollapsed}
              className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer hidden md:flex items-center justify-center"
              title="Collapse sidebar (Ctrl+B)"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </>
        ) : (
          <div className="w-full flex justify-center">
            <button
              onClick={toggleCollapsed}
              className="w-9 h-9 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center text-primary hover:bg-primary/20 transition-colors group cursor-pointer"
              title="Expand sidebar (Ctrl+B)"
            >
              <PanelLeftOpen className="w-4 h-4 transition-transform group-hover:scale-110" />
            </button>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-3 px-2 space-y-1 overflow-y-auto">
        {routes.map((route) => {
          const isActive =
            pathname === route.path ||
            (route.path !== '/' && pathname.startsWith(route.path));

          if (collapsed) {
            return (
              <Link
                key={route.path}
                href={route.path}
                title={route.name}
                className={cn(
                  'relative w-10 h-10 mx-auto flex items-center justify-center rounded-lg transition-colors group',
                  isActive
                    ? 'bg-primary/15 text-primary border border-primary/25 shadow-xs'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
                )}
              >
                <route.icon className="w-4 h-4 shrink-0 transition-transform group-hover:scale-110" />
              </Link>
            );
          }

          return (
            <Link
              key={route.path}
              href={route.path}
              className={cn(
                'relative flex items-center gap-3 px-3 py-2 rounded-lg text-xs font-medium transition-colors group',
                isActive
                  ? 'text-primary'
                  : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
              )}
            >
              {isActive && (
                <motion.div
                  layoutId="sidebar-active"
                  className="absolute inset-0 bg-primary/10 rounded-lg border border-primary/20"
                  initial={false}
                  transition={{
                    type: 'spring',
                    stiffness: 350,
                    damping: 30,
                  }}
                />
              )}
              <route.icon
                className={cn(
                  'w-4 h-4 shrink-0 relative z-10 transition-transform group-hover:scale-105',
                  isActive ? 'text-primary' : 'text-muted-foreground group-hover:text-foreground'
                )}
              />
              <span className="relative z-10 truncate">{route.name}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer / Identity */}
      <div
        className={cn(
          'border-t border-border shrink-0 transition-all',
          collapsed ? 'p-2 flex justify-center' : 'p-3 flex flex-col gap-1.5'
        )}
      >
        {collapsed ? (
          <Link
            href="/settings"
            title={`Identity: ${apiKey ? 'from API key' : 'anonymous'}`}
            className={cn(
              'w-9 h-9 rounded-lg flex items-center justify-center border transition-colors',
              apiKey
                ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400 hover:bg-emerald-500/20'
                : 'bg-muted/50 border-border/80 text-muted-foreground hover:bg-muted'
            )}
          >
            <Key className="w-4 h-4" />
          </Link>
        ) : (
          <>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-muted-foreground flex items-center gap-1.5">
                <Key className="w-3.5 h-3.5" />
                <span>Identity</span>
              </span>
              <span
                className={cn(
                  'font-mono text-[10px] font-semibold px-2 py-0.5 rounded border truncate max-w-[120px]',
                  apiKey
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                    : 'bg-muted text-muted-foreground border-border/80'
                )}
              >
                {apiKey ? 'from API key' : 'anonymous'}
              </span>
            </div>
            <div className="flex items-center justify-between text-[10px] text-muted-foreground/60 px-0.5">
              <span>Echo Gate v1.0</span>
              <Link
                href="/settings"
                className="hover:text-foreground transition-colors underline underline-offset-2"
              >
                Configure
              </Link>
            </div>
          </>
        )}
      </div>
    </div>
  );

  return (
    <>
      {/* Mobile-only toggle button */}
      <button
        onClick={() => setIsMobileOpen(!isMobileOpen)}
        className="md:hidden fixed top-3 left-3 z-50 p-2 bg-card/90 backdrop-blur-xs border border-border rounded-lg shadow-sm hover:bg-muted text-foreground transition-colors cursor-pointer"
        aria-label="Toggle navigation"
      >
        {isMobileOpen ? <X className="w-4 h-4" /> : <Menu className="w-4 h-4" />}
      </button>

      {/* Mobile Drawer (Overlay) */}
      <AnimatePresence>
        {isMobileOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsMobileOpen(false)}
              className="md:hidden fixed inset-0 bg-background/80 backdrop-blur-xs z-40"
            />
            <motion.aside
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: 'spring', damping: 25, stiffness: 280 }}
              className="md:hidden fixed top-0 left-0 bottom-0 w-64 bg-card border-r border-border z-50 flex flex-col shadow-2xl"
            >
              {sidebarContent(false)}
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* Desktop Sidebar (Permanent, in normal document flow, collapsible) */}
      <aside
        className={cn(
          'hidden md:flex flex-col shrink-0 h-full border-r border-border bg-card transition-[width] duration-200 overflow-hidden relative z-30',
          isCollapsed ? 'w-16' : 'w-64'
        )}
      >
        {sidebarContent(isCollapsed)}
      </aside>
    </>
  );
}

'use client';

import { useApiKey } from '@/context/api-key-context';
import { useAuth } from '@/context/auth-context';
import { cn } from '@/lib/utils';
import { AnimatePresence, motion } from 'framer-motion';
import {
  Activity,
  BarChart3,
  Database,
  GaugeCircle,
  Home,
  Key,
  LogIn,
  LogOut,
  Menu,
  MessagesSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  Settings,
  Sparkles,
  Terminal,
  UserPlus,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

const SIDEBAR_COLLAPSED_KEY = 'echo_gate_sidebar_collapsed';

export function AppSidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { apiKey } = useApiKey();
  const { user, tenantId, signOut } = useAuth();

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
  const [signingOut, setSigningOut] = useState(false);

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

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut();
      router.push('/');
      router.refresh();
    } finally {
      setSigningOut(false);
    }
  };

  const routes = [
    { name: 'Playground', path: '/playground', icon: Terminal },
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
              title="Back to Echo Gate Home"
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
                    Gateway Console
                  </span>
                </div>
              </div>
            </Link>
            <button
              type="button"
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
              type="button"
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
      <nav
        aria-label="Console"
        className="flex-1 py-3 px-2 space-y-1 overflow-y-auto"
      >
        {!collapsed && (
          <Link
            href="/"
            className="flex items-center gap-3 px-3 py-1.5 mb-1 rounded-lg text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          >
            <Home className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate">Home &amp; Overview</span>
          </Link>
        )}
        {routes.map((route) => {
          const isActive =
            pathname === route.path || pathname.startsWith(`${route.path}/`);

          if (collapsed) {
            return (
              <Link
                key={route.path}
                href={route.path}
                aria-current={isActive ? 'page' : undefined}
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
              aria-current={isActive ? 'page' : undefined}
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
                  isActive
                    ? 'text-primary'
                    : 'text-muted-foreground group-hover:text-foreground'
                )}
              />
              <span className="relative z-10 truncate">{route.name}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer / Auth & Identity */}
      <div
        className={cn(
          'border-t border-border shrink-0 transition-all',
          collapsed
            ? 'p-2 flex flex-col items-center gap-2'
            : 'p-3 flex flex-col gap-2'
        )}
      >
        {collapsed ? (
          <>
            <Link
              href="/settings"
              title={
                user
                  ? `Account: ${user.email} (${tenantId})`
                  : `Identity: ${apiKey ? 'from API key' : 'anonymous'}`
              }
              className={cn(
                'w-9 h-9 rounded-lg flex items-center justify-center border transition-colors',
                user || apiKey
                  ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400 hover:bg-emerald-500/20'
                  : 'bg-muted/50 border-border/80 text-muted-foreground hover:bg-muted'
              )}
            >
              <Key className="w-4 h-4" />
            </Link>
            {user ? (
              <button
                type="button"
                disabled={signingOut}
                onClick={handleSignOut}
                title="Sign out"
                aria-label="Sign out"
                className="w-9 h-9 rounded-lg flex items-center justify-center border border-border/70 bg-card hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer disabled:opacity-50"
              >
                <LogOut className="w-4 h-4" />
              </button>
            ) : (
              <Link
                href="/login"
                title="Sign in"
                aria-label="Sign in"
                className="w-9 h-9 rounded-lg flex items-center justify-center border border-primary/30 bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
              >
                <LogIn className="w-4 h-4" />
              </Link>
            )}
          </>
        ) : (
          <>
            {user ? (
              <div className="space-y-2 pb-2 border-b border-border/40">
                <div className="flex items-center justify-between text-[11px] gap-2">
                  <div className="truncate text-foreground font-medium">
                    {user.email}
                  </div>
                  <span className="font-mono text-[9px] bg-primary/10 text-primary border border-primary/20 px-1.5 py-0.5 rounded truncate max-w-[84px] shrink-0">
                    {tenantId}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  <Link
                    href="/settings"
                    className="inline-flex items-center justify-center gap-1 rounded-lg border border-border/80 bg-muted/40 hover:bg-muted px-2 py-1.5 text-[11px] font-medium text-foreground transition-colors"
                  >
                    <Key className="w-3 h-3 text-primary" />
                    <span>API Keys</span>
                  </Link>
                  <button
                    type="button"
                    disabled={signingOut}
                    onClick={handleSignOut}
                    className="inline-flex items-center justify-center gap-1 rounded-lg border border-border/80 bg-card hover:bg-muted px-2 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors cursor-pointer disabled:opacity-50"
                  >
                    <LogOut className="w-3 h-3" />
                    <span>{signingOut ? '...' : 'Sign out'}</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-1.5 pb-2 border-b border-border/40">
                <div className="grid grid-cols-2 gap-1.5">
                  <Link
                    href="/login"
                    className="inline-flex items-center justify-center gap-1 rounded-lg border border-border/80 bg-card hover:bg-muted px-2 py-1.5 text-[11px] font-medium text-foreground transition-colors"
                  >
                    <LogIn className="w-3 h-3 text-primary" />
                    <span>Sign in</span>
                  </Link>
                  <Link
                    href="/signup"
                    className="inline-flex items-center justify-center gap-1 rounded-lg bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 px-2 py-1.5 text-[11px] font-semibold text-white transition-all"
                  >
                    <UserPlus className="w-3 h-3" />
                    <span>Sign up</span>
                  </Link>
                </div>
              </div>
            )}

            <div className="flex items-center justify-between pt-0.5">
              <span className="text-[11px] font-medium text-muted-foreground flex items-center gap-1.5">
                <Key className="w-3.5 h-3.5" />
                <span>Gateway Key</span>
              </span>
              <span
                className={cn(
                  'font-mono text-[10px] font-semibold px-2 py-0.5 rounded border truncate max-w-[120px]',
                  apiKey
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                    : 'bg-muted text-muted-foreground border-border/80'
                )}
              >
                {apiKey ? 'configured' : 'anonymous'}
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
        type="button"
        onClick={() => setIsMobileOpen(!isMobileOpen)}
        className="md:hidden fixed top-3 left-3 z-50 p-2 bg-card/90 backdrop-blur-xs border border-border rounded-lg shadow-sm hover:bg-muted text-foreground transition-colors cursor-pointer"
        aria-label="Toggle navigation"
      >
        {isMobileOpen ? (
          <X className="w-4 h-4" />
        ) : (
          <Menu className="w-4 h-4" />
        )}
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

'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  MessageSquare,
  BarChart3,
  Settings,
  Database,
  Menu,
  X,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { motion, AnimatePresence } from 'framer-motion';
import { useState } from 'react';

export function AppSidebar() {
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false); // Default to closed on mobile, or we could detect screen size, but let's default to false and let the user open it. Actually, for a desktop-first app, maybe true is better. Let's use false so they don't overlap by default if we don't have hydration checks. Let's just use true for now, but hidden behind a toggle.

  const routes = [
    { name: 'Chat', path: '/', icon: MessageSquare },
    { name: 'Analytics', path: '/analytics', icon: BarChart3 },
    { name: 'Cache DB', path: '/cache', icon: Database },
    { name: 'Settings', path: '/settings', icon: Settings },
  ];

  return (
    <>
      {/* Floating Toggle Button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="fixed top-4 left-4 z-50 p-2 bg-card border border-border rounded-md shadow-sm hover:bg-muted transition-colors text-foreground"
      >
        {isOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>

      {/* Sidebar Content */}
      <AnimatePresence mode="wait">
        {isOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0, x: -50 }}
            animate={{ width: 256, opacity: 1, x: 0 }}
            exit={{ width: 0, opacity: 0, x: -50 }}
            className="border-r border-border bg-card flex flex-col shrink-0 z-40 fixed md:relative h-screen overflow-hidden"
          >
            <div className="h-16 flex items-center px-16 border-b border-border shrink-0 w-64">
              <h1 className="text-xl font-bold text-foreground">Echo Gate</h1>
            </div>

            <nav className="flex-1 py-6 px-4 space-y-2 overflow-y-auto w-64">
              {routes.map((route) => {
                const isActive = pathname === route.path;
                return (
                  <Link
                    key={route.path}
                    href={route.path}
                    className="relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors hover:text-foreground text-muted-foreground hover:bg-muted/50"
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
                        'w-5 h-5 relative z-10',
                        isActive ? 'text-primary' : ''
                      )}
                    />
                    <span
                      className={cn(
                        'relative z-10',
                        isActive ? 'text-primary' : ''
                      )}
                    >
                      {route.name}
                    </span>
                  </Link>
                );
              })}
            </nav>

            <div className="p-4 border-t border-border text-xs text-muted-foreground text-center shrink-0 w-64">
              Echo Gate API
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}

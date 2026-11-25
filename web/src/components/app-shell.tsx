'use client';

import { AppSidebar } from '@/components/app-sidebar';
import { SiteNavbar } from '@/components/site-navbar';
import { usePathname } from 'next/navigation';
import React from 'react';

const PUBLIC_SHELL_PREFIXES = [
  '/',
  '/login',
  '/signup',
  '/forgot-password',
  '/reset-password',
];

function isPublicShellRoute(pathname: string): boolean {
  if (pathname === '/') return true;
  return PUBLIC_SHELL_PREFIXES.some(
    (prefix) => prefix !== '/' && pathname.startsWith(prefix)
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublicShell = isPublicShellRoute(pathname);

  if (isPublicShell) {
    return (
      <div className="flex-1 flex flex-col min-h-dvh w-full overflow-y-auto bg-background">
        <SiteNavbar />
        <main className="flex-1 flex flex-col w-full min-w-0">{children}</main>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-row h-dvh w-full overflow-hidden">
      <React.Suspense fallback={null}>
        <AppSidebar />
      </React.Suspense>
      <main className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
        {children}
      </main>
    </div>
  );
}

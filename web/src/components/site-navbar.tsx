'use client';

import { useAuth } from '@/context/auth-context';
import { cn } from '@/lib/utils';
import {
  KeyRound,
  LogIn,
  LogOut,
  Menu,
  Sparkles,
  UserPlus,
} from 'lucide-react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

export function SiteNavbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, tenantId, isLoading, signOut } = useAuth();

  const headerRef = useRef<HTMLElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);

  const [isWide, setIsWide] = useState(false);
  const [isMeasuring, setIsMeasuring] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  useEffect(() => {
    const header = headerRef.current;
    const inner = innerRef.current;
    const menu = menuRef.current;
    if (!header || !inner || !menu) return;

    let lastHeaderWidth = header.clientWidth;

    const measure = () => {
      const wasOpen = menu.matches(':popover-open');
      if (wasOpen && menu.hidePopover) {
        try {
          menu.hidePopover();
        } catch {
          // ignore
        }
      }

      setIsMeasuring(true);
      header.classList.add('is-measuring', 'is-wide');
      menu.removeAttribute('popover');

      const fits = inner.scrollWidth <= inner.clientWidth + 1;

      if (fits) {
        setIsWide(true);
        header.classList.add('is-wide');
        menu.removeAttribute('popover');
      } else {
        setIsWide(false);
        header.classList.remove('is-wide');
        menu.setAttribute('popover', 'auto');
        if (wasOpen && menu.showPopover) {
          try {
            menu.showPopover();
          } catch {
            // ignore
          }
        }
      }

      requestAnimationFrame(() => {
        header.classList.remove('is-measuring');
        setIsMeasuring(false);
      });
    };

    measure();

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const nextWidth = entry.contentRect.width;
      // Only re-measure while collapsed if the header actually grew
      if (
        !header.classList.contains('is-wide') &&
        nextWidth <= lastHeaderWidth
      ) {
        lastHeaderWidth = nextWidth;
        return;
      }
      lastHeaderWidth = nextWidth;
      measure();
    });

    observer.observe(header);
    observer.observe(menu);

    return () => observer.disconnect();
  }, [user, isLoading]);

  const closeMenuPopover = () => {
    const menu = menuRef.current;
    if (menu && menu.hasAttribute('popover') && menu.matches(':popover-open')) {
      try {
        menu.hidePopover();
      } catch {
        // ignore
      }
    }
  };

  const handleSignOut = async () => {
    setSigningOut(true);
    try {
      await signOut();
      closeMenuPopover();
      router.push('/');
      router.refresh();
    } finally {
      setSigningOut(false);
    }
  };

  const navItems = [
    { label: 'Features', href: '/#features' },
    { label: 'Quickstart', href: '/#quickstart' },
    { label: 'Playground', href: '/playground' },
    { label: 'Analytics', href: '/analytics' },
    { label: 'Traces', href: '/traces' },
  ];

  return (
    <header
      ref={headerRef}
      className={cn(
        'site-header sticky top-0 z-40 border-b border-border/70 bg-background/85 backdrop-blur-md',
        isWide && 'is-wide',
        isMeasuring && 'is-measuring'
      )}
    >
      <div
        ref={innerRef}
        className="header-inner max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4"
      >
        <Link
          href="/"
          aria-current={pathname === '/' ? 'page' : undefined}
          className="site-logo flex items-center gap-2.5 shrink-0 group"
        >
          <div className="w-8 h-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center text-primary group-hover:bg-primary/20 transition-colors">
            <Sparkles className="w-4 h-4" aria-hidden="true" />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold tracking-tight text-foreground">
              Echo Gate
            </span>
            <span className="hidden sm:inline-flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[10px] font-mono text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              Cloud
            </span>
          </div>
        </Link>

        <nav className="site-nav flex items-center" aria-label="Primary">
          <button
            type="button"
            className="menu-button inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-xs font-medium text-foreground hover:bg-muted transition-colors cursor-pointer"
            popoverTarget="site-menu"
            popoverTargetAction="toggle"
            aria-controls="site-menu"
          >
            <Menu className="w-4 h-4" aria-hidden="true" />
            <span>Menu</span>
          </button>

          <ul
            ref={menuRef}
            id="site-menu"
            popover="auto"
            className="site-menu rounded-2xl border border-border bg-card p-3 shadow-2xl space-y-1 md:space-y-0"
          >
            {navItems.map((item) => {
              const isCurrent =
                item.href !== '/#features' &&
                item.href !== '/#quickstart' &&
                (pathname === item.href ||
                  (item.href !== '/' && pathname.startsWith(item.href)));
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={isCurrent ? 'page' : undefined}
                    onClick={closeMenuPopover}
                    className={cn(
                      'nav-link block rounded-lg px-3 py-2 text-xs font-medium transition-colors whitespace-nowrap',
                      isCurrent
                        ? 'bg-primary/15 text-primary border border-primary/25'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
                    )}
                  >
                    {item.label}
                  </Link>
                </li>
              );
            })}

            <li
              className="hidden sm:block h-4 w-px bg-border/80 mx-1.5 shrink-0"
              aria-hidden="true"
            />

            {user ? (
              <>
                <li>
                  <Link
                    href="/settings"
                    aria-current={pathname === '/settings' ? 'page' : undefined}
                    onClick={closeMenuPopover}
                    className={cn(
                      'nav-link flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors whitespace-nowrap',
                      pathname === '/settings'
                        ? 'bg-primary/15 text-primary border border-primary/25'
                        : 'text-foreground bg-muted/40 hover:bg-muted border border-border/70'
                    )}
                    title={tenantId ? `Tenant: ${tenantId}` : undefined}
                  >
                    <KeyRound
                      className="w-3.5 h-3.5 text-primary"
                      aria-hidden="true"
                    />
                    <span className="max-w-[160px] truncate">
                      {user.email || 'API Keys'}
                    </span>
                  </Link>
                </li>
                <li>
                  <button
                    type="button"
                    disabled={signingOut}
                    onClick={handleSignOut}
                    className="w-full sm:w-auto inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/70 border border-transparent hover:border-border/70 transition-colors cursor-pointer whitespace-nowrap disabled:opacity-50"
                  >
                    <LogOut className="w-3.5 h-3.5" aria-hidden="true" />
                    <span>{signingOut ? 'Signing out...' : 'Sign out'}</span>
                  </button>
                </li>
              </>
            ) : (
              <>
                <li>
                  <Link
                    href="/login"
                    aria-current={pathname === '/login' ? 'page' : undefined}
                    onClick={closeMenuPopover}
                    className={cn(
                      'nav-link flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition-colors whitespace-nowrap',
                      pathname === '/login'
                        ? 'bg-primary/15 text-primary border border-primary/25'
                        : 'text-muted-foreground hover:text-foreground hover:bg-muted/60'
                    )}
                  >
                    <LogIn className="w-3.5 h-3.5" aria-hidden="true" />
                    <span>Sign in</span>
                  </Link>
                </li>
                <li>
                  <Link
                    href="/signup"
                    aria-current={pathname === '/signup' ? 'page' : undefined}
                    onClick={closeMenuPopover}
                    className="flex items-center justify-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold text-white bg-gradient-to-r from-blue-600 via-indigo-600 to-blue-500 hover:from-blue-500 hover:to-indigo-500 shadow-sm shadow-blue-500/20 transition-all whitespace-nowrap"
                  >
                    <UserPlus className="w-3.5 h-3.5" aria-hidden="true" />
                    <span>Sign up</span>
                  </Link>
                </li>
              </>
            )}
          </ul>
        </nav>
      </div>
    </header>
  );
}

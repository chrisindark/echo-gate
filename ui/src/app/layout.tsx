import { AppSidebar } from '@/components/app-sidebar';
import { ApiKeyProvider } from '@/context/api-key-context';
import { Theme } from '@radix-ui/themes';
import '@radix-ui/themes/styles.css';
import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import React from 'react';
import './globals.css';

const geistSans = Geist({
  variable: '--font-geist-sans',
  subsets: ['latin'],
});

const geistMono = Geist_Mono({
  variable: '--font-geist-mono',
  subsets: ['latin'],
});

export const metadata: Metadata = {
  title: 'Echo Gate',
  description: 'Semantic Caching LLM Gateway',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <Theme
          appearance="dark"
          accentColor="blue"
          radius="medium"
          className="flex-1 flex flex-row"
        >
          <ApiKeyProvider>
            <React.Suspense fallback={null}>
              <AppSidebar />
            </React.Suspense>
            <main className="flex-1 flex flex-col min-h-screen overflow-x-hidden">
              {children}
            </main>
          </ApiKeyProvider>
        </Theme>
      </body>
    </html>
  );
}

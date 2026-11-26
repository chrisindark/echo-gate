/* eslint-disable @typescript-eslint/no-unused-vars */
'use client';

import { cn } from '@/lib/utils';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface MessageContentProps {
  content: string;
  className?: string;
  isStreaming?: boolean;
}

export function MessageContent({
  content,
  className,
  isStreaming = false,
}: MessageContentProps) {
  return (
    <div
      className={cn(
        'prose-chat leading-relaxed break-words [overflow-wrap:anywhere] text-sm sm:text-base',
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noreferrer noopener"
              className="text-primary underline underline-offset-2 hover:text-primary/80"
            />
          ),
          pre: ({ node, ...props }) => (
            <pre
              {...props}
              className="my-2 overflow-x-auto rounded-xl bg-muted/40 border border-border/60 p-3 font-mono text-xs leading-relaxed"
            />
          ),
          code: ({ node, className, children, ...props }) => {
            const isBlock = Boolean(className?.includes('language-'));
            if (isBlock) {
              return (
                <code
                  {...props}
                  className={cn('font-mono text-emerald-300', className)}
                >
                  {children}
                </code>
              );
            }
            return (
              <code
                {...props}
                className="rounded bg-muted/60 px-1 py-0.5 font-mono text-[0.85em] text-foreground"
              >
                {children}
              </code>
            );
          },
          p: ({ node, ...props }) => (
            <p {...props} className="my-2 first:mt-0 last:mb-0" />
          ),
          ul: ({ node, ...props }) => (
            <ul {...props} className="my-2 list-disc pl-5 space-y-1" />
          ),
          ol: ({ node, ...props }) => (
            <ol {...props} className="my-2 list-decimal pl-5 space-y-1" />
          ),
          h1: ({ node, ...props }) => (
            <h1 {...props} className="mt-3 mb-1 text-lg font-semibold" />
          ),
          h2: ({ node, ...props }) => (
            <h2 {...props} className="mt-3 mb-1 text-base font-semibold" />
          ),
          h3: ({ node, ...props }) => (
            <h3 {...props} className="mt-2 mb-1 text-sm font-semibold" />
          ),
          blockquote: ({ node, ...props }) => (
            <blockquote
              {...props}
              className="my-2 border-l-2 border-border pl-3 text-muted-foreground italic"
            />
          ),
          table: ({ node, ...props }) => (
            <div className="my-2 overflow-x-auto">
              <table {...props} className="w-full border-collapse text-xs" />
            </div>
          ),
          th: ({ node, ...props }) => (
            <th
              {...props}
              className="border border-border/60 bg-muted/40 px-2 py-1 text-left font-semibold"
            />
          ),
          td: ({ node, ...props }) => (
            <td {...props} className="border border-border/60 px-2 py-1" />
          ),
          hr: ({ node, ...props }) => (
            <hr {...props} className="my-3 border-border/60" />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
      {isStreaming && (
        <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-xs bg-primary align-middle" />
      )}
    </div>
  );
}

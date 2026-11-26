'use client';

import { cn } from '@/lib/utils';
import React, { forwardRef, useCallback, useEffect, useRef } from 'react';
import { Send, Square, X } from 'lucide-react';

export interface ChatComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onStop?: () => void;
  loading?: boolean;
  disabled?: boolean;
  placeholder?: string;
  hint?: string;
  tokenCount?: number;
  autoFocus?: boolean;
  className?: string;
}

const MAX_ROWS = 8;
const LINE_HEIGHT = 22;

export const ChatComposer = forwardRef<HTMLTextAreaElement, ChatComposerProps>(
  function ChatComposer(
    {
      value,
      onChange,
      onSubmit,
      onStop,
      loading = false,
      disabled = false,
      placeholder = 'Message Echo Gate…',
      hint,
      tokenCount,
      autoFocus = false,
      className,
    },
    forwardedRef
  ) {
    const innerRef = useRef<HTMLTextAreaElement | null>(null);
    const setRef = useCallback(
      (node: HTMLTextAreaElement | null) => {
        innerRef.current = node;
        if (typeof forwardedRef === 'function') {
          forwardedRef(node);
        } else if (forwardedRef) {
          forwardedRef.current = node;
        }
      },
      [forwardedRef]
    );

    const resize = useCallback(() => {
      const el = innerRef.current;
      if (!el) return;
      el.style.height = 'auto';
      const max = MAX_ROWS * LINE_HEIGHT + 16;
      const next = Math.min(el.scrollHeight, max);
      el.style.height = `${Math.max(44, next)}px`;
      el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden';
    }, []);

    useEffect(() => {
      resize();
    }, [value, resize]);

    useEffect(() => {
      if (autoFocus) {
        innerRef.current?.focus();
      }
    }, [autoFocus]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!loading && value.trim() && !disabled) {
          onSubmit();
        }
      }
    };

    const canSend = !loading && !!value.trim() && !disabled;

    return (
      <div
        className={cn(
          'rounded-2xl border border-border bg-card shadow-sm transition-shadow focus-within:ring-1 focus-within:ring-primary/50',
          className
        )}
      >
        <div className="relative">
          <textarea
            ref={setRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            disabled={disabled}
            rows={1}
            className="w-full resize-none bg-transparent px-4 py-3 pr-12 text-sm text-foreground placeholder:text-muted-foreground outline-none disabled:cursor-not-allowed disabled:opacity-60"
            style={{ minHeight: 44 }}
          />
          {value && !loading && (
            <button
              type="button"
              onClick={() => onChange('')}
              className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              aria-label="Clear input"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="flex items-center justify-between gap-2 border-t border-border/60 px-3 py-2">
          <span className="text-[11px] text-muted-foreground">
            {hint ?? 'Enter to send · Shift+Enter for newline'}
          </span>
          <div className="flex items-center gap-2">
            {typeof tokenCount === 'number' && tokenCount > 0 && (
              <span className="font-mono text-[11px] text-muted-foreground">
                ~{tokenCount} tok
              </span>
            )}
            {loading ? (
              <button
                type="button"
                onClick={onStop}
                className="inline-flex items-center gap-1.5 rounded-lg bg-card border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted"
              >
                <Square className="h-3.5 w-3.5 fill-destructive text-destructive" />
                Stop
              </button>
            ) : (
              <button
                type="button"
                onClick={onSubmit}
                disabled={!canSend}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors',
                  canSend
                    ? 'bg-primary text-primary-foreground hover:bg-primary/90'
                    : 'cursor-not-allowed bg-muted text-muted-foreground'
                )}
              >
                <Send className="h-3.5 w-3.5" />
                Send
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }
);

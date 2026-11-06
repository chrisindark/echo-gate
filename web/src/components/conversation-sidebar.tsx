'use client';

import { AnimatePresence, motion } from 'framer-motion';
import {
  Check,
  Edit3,
  MessageSquare,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
  X,
} from 'lucide-react';
import React, { useState, useSyncExternalStore } from 'react';

import { ConversationItem } from '@/lib/conversations';
import { cn } from '@/lib/utils';

interface ConversationSidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  conversations: ConversationItem[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  onDeleteConversation: (id: string) => Promise<void>;
  onRenameConversation: (id: string, newTitle: string) => Promise<void>;
  loading?: boolean;
}

const emptySubscribe = () => () => {};

function useClientTime(): number | null {
  return useSyncExternalStore(
    emptySubscribe,
    () => Date.now(),
    () => null
  );
}

function groupConversationsByDate(
  conversations: ConversationItem[],
  nowMs: number | null
) {
  if (!nowMs) {
    return conversations.length > 0
      ? [{ label: 'Conversations', items: conversations }]
      : [];
  }

  const groups: { label: string; items: ConversationItem[] }[] = [
    { label: 'Today', items: [] },
    { label: 'Yesterday', items: [] },
    { label: 'Previous 7 Days', items: [] },
    { label: 'Older', items: [] },
  ];

  const now = new Date(nowMs);
  const today = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate()
  ).getTime();
  const yesterday = today - 86400000;
  const last7Days = today - 7 * 86400000;

  for (const conv of conversations) {
    const time = new Date(conv.updated_at || conv.created_at).getTime();
    if (time >= today) {
      groups[0].items.push(conv);
    } else if (time >= yesterday) {
      groups[1].items.push(conv);
    } else if (time >= last7Days) {
      groups[2].items.push(conv);
    } else {
      groups[3].items.push(conv);
    }
  }

  return groups.filter((g) => g.items.length > 0);
}

export function ConversationSidebar({
  isOpen,
  onToggle,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onRenameConversation,
  loading = false,
}: ConversationSidebarProps) {
  const mountedTime = useClientTime();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const startEditing = (conv: ConversationItem, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(conv.id);
    setEditTitle(conv.title || 'New Conversation');
  };

  const cancelEditing = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setEditingId(null);
    setEditTitle('');
  };

  const handleSaveRename = async (id: string, e?: React.FormEvent) => {
    e?.preventDefault();
    if (!editTitle.trim()) return;
    try {
      await onRenameConversation(id, editTitle.trim());
      setEditingId(null);
    } catch {
      // Handled in parent
    }
  };

  const confirmDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      setDeletingId(id);
      await onDeleteConversation(id);
    } finally {
      setDeletingId(null);
    }
  };

  const grouped = groupConversationsByDate(conversations, mountedTime);

  return (
    <>
      {/* Collapsed Toggle Button */}
      {!isOpen && (
        <button
          onClick={onToggle}
          title="Open Conversation History"
          className="fixed top-4 left-16 z-30 p-2 bg-card border border-border rounded-md shadow-sm hover:bg-muted transition-colors text-foreground"
        >
          <PanelLeftOpen className="w-5 h-5" />
        </button>
      )}

      {/* Sidebar Drawer */}
      <AnimatePresence>
        {isOpen && (
          <motion.aside
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 280, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex flex-col border-r border-border bg-card shrink-0 h-full overflow-hidden relative z-30"
          >
            {/* Header */}
            <div className="p-4 pt-16 flex items-center justify-between border-b border-border shrink-0">
              <div className="flex items-center gap-2 font-semibold text-sm text-foreground">
                <MessageSquare className="w-4 h-4 text-primary" />
                <span>Conversations</span>
              </div>
              <button
                onClick={onToggle}
                title="Collapse Conversations"
                className="p-1.5 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              >
                <PanelLeftClose className="w-4 h-4" />
              </button>
            </div>

            {/* New Chat Button */}
            <div className="p-3 border-b border-border">
              <button
                onClick={onNewConversation}
                className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-primary text-primary-foreground font-medium text-sm shadow-sm hover:bg-primary/90 transition-all cursor-pointer"
              >
                <Plus className="w-4 h-4" />
                <span>New Chat</span>
              </button>
            </div>

            {/* Thread List */}
            <div className="flex-1 overflow-y-auto p-2 space-y-4">
              {loading && conversations.length === 0 ? (
                <div className="p-4 text-center text-xs text-muted-foreground">
                  Loading chats...
                </div>
              ) : conversations.length === 0 ? (
                <div className="p-6 text-center space-y-2">
                  <div className="w-10 h-10 rounded-full bg-muted/60 flex items-center justify-center mx-auto text-muted-foreground">
                    <MessageSquarePlus className="w-5 h-5" />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    No conversations yet. Start a new chat!
                  </p>
                </div>
              ) : (
                grouped.map((group) => (
                  <div key={group.label} className="space-y-1">
                    <div className="px-2 py-1 text-[11px] font-semibold tracking-wider uppercase text-muted-foreground/70">
                      {group.label}
                    </div>
                    {group.items.map((conv) => {
                      const isActive = conv.id === activeConversationId;
                      const isEditing = conv.id === editingId;
                      const isDeleting = conv.id === deletingId;

                      if (isEditing) {
                        return (
                          <form
                            key={conv.id}
                            onSubmit={(e) => handleSaveRename(conv.id, e)}
                            className="flex items-center gap-1 px-2 py-1.5 bg-muted/70 rounded-lg border border-primary/30"
                          >
                            <input
                              type="text"
                              value={editTitle}
                              onChange={(e) => setEditTitle(e.target.value)}
                              autoFocus
                              className="flex-1 bg-transparent text-xs text-foreground focus:outline-none px-1"
                            />
                            <button
                              type="submit"
                              className="p-1 hover:text-emerald-500 text-muted-foreground"
                              title="Save"
                            >
                              <Check className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={cancelEditing}
                              className="p-1 hover:text-destructive text-muted-foreground"
                              title="Cancel"
                            >
                              <X className="w-3.5 h-3.5" />
                            </button>
                          </form>
                        );
                      }

                      return (
                        <div
                          key={conv.id}
                          onClick={() => onSelectConversation(conv.id)}
                          className={cn(
                            'group relative flex items-center justify-between px-3 py-2 rounded-lg text-xs font-medium cursor-pointer transition-colors',
                            isActive
                              ? 'bg-primary/10 text-primary border border-primary/20'
                              : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                          )}
                        >
                          <span className="truncate pr-2 select-none flex-1">
                            {conv.title || 'New Conversation'}
                          </span>

                          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={(e) => startEditing(conv, e)}
                              className="p-1 text-muted-foreground hover:text-foreground rounded hover:bg-background/80"
                              title="Rename"
                            >
                              <Edit3 className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={(e) => confirmDelete(conv.id, e)}
                              disabled={isDeleting}
                              className="p-1 text-muted-foreground hover:text-destructive rounded hover:bg-background/80"
                              title="Delete"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </>
  );
}

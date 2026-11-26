'use client';

import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';

const NEAR_BOTTOM_PX = 80;

export function useStickToBottom(dependencies: ReadonlyArray<unknown>): {
  containerRef: RefObject<HTMLDivElement | null>;
  isNearBottom: boolean;
  jumpToBottom: (behavior?: ScrollBehavior) => void;
} {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [isNearBottom, setIsNearBottom] = useState(true);
  const followingRef = useRef(true);

  const jumpToBottom = (behavior: ScrollBehavior = 'smooth') => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
    followingRef.current = true;
    setIsNearBottom(true);
  };

  // Track whether the user is near the bottom.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      const near = distance <= NEAR_BOTTOM_PX;
      followingRef.current = near;
      setIsNearBottom(near);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Follow content growth when we were already at the bottom.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      if (followingRef.current) {
        el.scrollTop = el.scrollHeight;
      }
    });
    observer.observe(el);
    // Also observe the first child (content wrapper) if present.
    if (el.firstElementChild) {
      observer.observe(el.firstElementChild);
    }
    return () => observer.disconnect();
  }, []);

  // Re-evaluate follow on dependency changes (streamed tokens, new messages).
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    if (followingRef.current) {
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight;
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);

  return { containerRef, isNearBottom, jumpToBottom };
}

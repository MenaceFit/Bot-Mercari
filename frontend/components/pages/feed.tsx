'use client';

import { FeedList } from '@/components/feed-list';
import { useStore } from '@/components/store';

export function FeedPage() {
  const { feed } = useStore();
  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-sm font-semibold">Live Feed</h1>
        <span className="text-2xs text-faint font-mono">
          {feed.length} annonce(s) en mémoire
        </span>
      </div>
      <FeedList limit={40} />
    </div>
  );
}

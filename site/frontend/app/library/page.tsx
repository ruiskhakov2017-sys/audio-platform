'use client';

import { useState } from 'react';
import { MOCK_STORIES } from '@/data/mocks';
import { StoryCard } from '@/components/v1/ui/StoryCard';
import Breadcrumbs from '@/components/v1/ui/Breadcrumbs';
import { History, Heart, Bookmark } from 'lucide-react';

type TabId = 'history' | 'favorites' | 'saved';

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: 'history', label: 'История прослушиваний', icon: <History className="h-4 w-4" /> },
  { id: 'favorites', label: 'Избранное', icon: <Heart className="h-4 w-4" /> },
  { id: 'saved', label: 'Сохранённые', icon: <Bookmark className="h-4 w-4" /> },
];

export default function LibraryPage() {
  const [activeTab, setActiveTab] = useState<TabId>('history');

  const stories = MOCK_STORIES.slice(0, 6);

  return (
    <div className="min-h-screen" style={{ backgroundColor: '#0c1222' }}>
      <div className="px-4 py-4 md:px-6">
        <Breadcrumbs
          items={[
            { href: '/', label: 'Главная' },
            { href: '/library', label: 'Моя коллекция' },
          ]}
        />
      </div>

      <div className="px-4 pb-12 md:px-6">
        <h1 className="mb-6 text-2xl font-bold md:text-3xl" style={{ color: '#f1f5f9' }}>
          Моя коллекция
        </h1>

        <div className="mb-8 flex gap-2 border-b" style={{ borderColor: 'rgba(59, 130, 246, 0.2)' }}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className="flex items-center gap-2 rounded-t-xl border-b-2 px-4 py-3 text-sm font-medium transition-colors"
              style={{
                borderColor: activeTab === tab.id ? '#2563eb' : 'transparent',
                color: activeTab === tab.id ? '#93c5fd' : '#94a3b8',
              }}
            >
              {tab.icon}
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'history' && (
          <p className="mb-4 text-sm" style={{ color: '#94a3b8' }}>
            Недавно прослушанные
          </p>
        )}
        {activeTab === 'favorites' && (
          <p className="mb-4 text-sm" style={{ color: '#94a3b8' }}>
            Добавлено в избранное
          </p>
        )}
        {activeTab === 'saved' && (
          <p className="mb-4 text-sm" style={{ color: '#94a3b8' }}>
            Сохранённые для прослушивания позже
          </p>
        )}

        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {stories.map((story) => (
            <StoryCard key={story.id} story={story} />
          ))}
        </div>
      </div>
    </div>
  );
}

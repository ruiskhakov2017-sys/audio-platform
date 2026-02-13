'use client';

import { useAuthStore } from '@/store/authStore';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { BookOpen, Users, Crown, Clock } from 'lucide-react';
import { fetchAdminStatsApi } from '@/lib/adminApi';

function formatSeconds(sec: number): string {
  if (sec < 60) return `${sec} сек`;
  const m = Math.floor(sec / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h} ч ${m % 60} мин`;
  return `${m} мин`;
}

export default function AdminDashboardPage() {
  const { isAuthenticated } = useAuthStore();
  const router = useRouter();
  const [stats, setStats] = useState<{
    total_users: number;
    premium_users: number;
    total_stories: number;
    total_listen_seconds: number;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) {
      router.push('/login');
      return;
    }
    fetchAdminStatsApi()
      .then((data) => {
        if (data) setStats(data);
        else setForbidden(true);
      })
      .catch(() => setForbidden(true))
      .finally(() => setLoading(false));
  }, [isAuthenticated, router]);

  useEffect(() => {
    if (!loading && (forbidden || (!stats && !loading))) {
      router.push('/');
    }
  }, [loading, forbidden, stats, router]);

  if (loading || !stats) {
    return (
      <div className="p-8 flex items-center justify-center min-h-[40vh]">
        <p className="text-zinc-500">Загрузка...</p>
      </div>
    );
  }

  const cards = [
    { label: 'Всего пользователей', value: stats.total_users.toLocaleString('ru-RU'), icon: Users },
    { label: 'Premium-пользователей', value: stats.premium_users.toLocaleString('ru-RU'), icon: Crown },
    { label: 'Всего историй', value: stats.total_stories.toLocaleString('ru-RU'), icon: BookOpen },
    { label: 'Общее время контента', value: formatSeconds(stats.total_listen_seconds), icon: Clock },
  ];

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold text-white mb-8">Статистика</h1>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
        {cards.map(({ label, value, icon: Icon }) => (
          <div
            key={label}
            className="rounded-xl border border-white/10 bg-white/5 p-6 hover:bg-white/[0.07] transition-colors"
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-sm text-zinc-400">{label}</span>
              <Icon className="w-5 h-5 text-[#00B4D8]/80" strokeWidth={1.5} />
            </div>
            <p className="text-2xl font-bold text-white">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

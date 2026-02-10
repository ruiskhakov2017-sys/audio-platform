'use client';

import { useAuthStore } from '@/store/authStore';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { BookOpen, Users, DollarSign, TrendingUp } from 'lucide-react';

export default function AdminDashboardPage() {
  const { profile } = useAuthStore();
  const router = useRouter();

  useEffect(() => {
    if (profile?.role && profile.role !== 'admin') {
      router.push('/');
      return;
    }
  }, [profile?.role, router]);

  const stats = [
    { label: 'Всего историй', value: '124', icon: BookOpen },
    { label: 'Пользователей', value: '1,204', icon: Users },
    { label: 'Выручка (Месяц)', value: '45,900 ₽', icon: DollarSign },
  ];

  const transactions = [
    { id: 1, email: 'user1@mail.ru', amount: '299 ₽', date: '07.02.2025', status: 'Оплачено' },
    { id: 2, email: 'user2@gmail.com', amount: '299 ₽', date: '07.02.2025', status: 'Оплачено' },
    { id: 3, email: 'user3@yandex.ru', amount: '299 ₽', date: '06.02.2025', status: 'Ожидание' },
  ];

  return (
    <div className="p-8">
      <h1 className="text-2xl font-semibold text-white mb-8">Дашборд</h1>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {stats.map(({ label, value, icon: Icon }) => (
          <div
            key={label}
            className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-5"
          >
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm text-zinc-500">{label}</span>
              <Icon className="w-5 h-5 text-zinc-500" />
            </div>
            <p className="text-xl font-semibold text-white">{value}</p>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-6 mb-8">
        <h2 className="text-lg font-medium text-white mb-4">Рост прослушиваний</h2>
        <div className="h-64 flex items-center justify-center rounded-lg bg-zinc-800/50 border border-zinc-700/50 text-zinc-500 text-sm gap-2">
          <TrendingUp className="w-8 h-8" />
          Визуальная заглушка графика (Recharts / Chart.js)
        </div>
      </div>

      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 overflow-hidden">
        <h2 className="text-lg font-medium text-white p-4 border-b border-zinc-800">
          Последние транзакции
        </h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-left text-zinc-500">
              <th className="p-4 font-medium">Email</th>
              <th className="p-4 font-medium">Сумма</th>
              <th className="p-4 font-medium">Дата</th>
              <th className="p-4 font-medium">Статус</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((row) => (
              <tr
                key={row.id}
                className="border-b border-zinc-800/50 hover:bg-zinc-800/30"
              >
                <td className="p-4 text-white">{row.email}</td>
                <td className="p-4 text-zinc-400">{row.amount}</td>
                <td className="p-4 text-zinc-400">{row.date}</td>
                <td className="p-4">
                  <span
                    className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                      row.status === 'Оплачено'
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : 'bg-amber-500/20 text-amber-400'
                    }`}
                  >
                    {row.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

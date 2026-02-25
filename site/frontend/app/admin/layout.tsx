'use client';

import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Upload, FileAudio, Users, LogOut } from 'lucide-react';
import { useAuthStore } from '@/store/authStore';
import { useEffect } from 'react';

const nav = [
  { href: '/admin', label: 'Дашборд', icon: LayoutDashboard },
  { href: '/admin/stories', label: 'Контент', icon: FileAudio },
  { href: '/admin/upload', label: 'Загрузить (1)', icon: Upload },
  { href: '/admin/upload/batch', label: 'Пакет (до 10)', icon: Upload },
  { href: '/admin/users', label: 'Пользователи', icon: Users },
];

const MOCK_ADMIN_ALLOW_DEV = true;

export default function AdminLayout({
  children,
}: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { profile, isAuthenticated, loading, logout } = useAuthStore();
  const isAdmin = profile?.role === 'admin';
  const showAccessDenied = !loading && isAuthenticated && !isAdmin && !MOCK_ADMIN_ALLOW_DEV;

  useEffect(() => {
    if (loading) return;
    if (!isAuthenticated && !MOCK_ADMIN_ALLOW_DEV) {
      router.push('/login?redirect=/admin');
      return;
    }
    if (isAuthenticated && !isAdmin && !MOCK_ADMIN_ALLOW_DEV) {
      return;
    }
  }, [loading, isAuthenticated, isAdmin, router]);

  const handleLogout = () => {
    logout();
    router.push('/');
  };

  if (showAccessDenied) {
    return (
      <div className="min-h-screen bg-zinc-950 text-zinc-200 flex items-center justify-center p-8">
        <div className="text-center max-w-md">
          <h1 className="text-xl font-semibold text-white mb-2">Доступ ограничен</h1>
          <p className="text-zinc-400 mb-6">
            Для входа в админ-панель нужна роль администратора. Обратитесь к владельцу сайта или задайте роль в Supabase (User Metadata: role = &quot;admin&quot;).
          </p>
          <Link
            href="/"
            className="inline-block px-4 py-2 rounded-lg bg-cyan-600 text-white hover:bg-cyan-500"
          >
            На главную
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-200 flex">
      <aside className="w-56 border-r border-zinc-800 bg-zinc-900/50 flex flex-col shrink-0 fixed left-0 top-0 bottom-0 z-10">
        <div className="p-4 border-b border-zinc-800">
          <Link href="/admin" className="text-lg font-semibold text-white">
            EroticAudio <span className="text-cyan-500">Admin</span>
          </Link>
        </div>
        <nav className="p-2 flex flex-col gap-0.5 flex-1">
          {nav.map(({ href, label, icon: Icon }) => {
            const isActive =
              pathname === href || (href !== '/admin' && pathname.startsWith(href));
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-zinc-800 text-white'
                    : 'text-zinc-400 hover:bg-zinc-800/50 hover:text-zinc-200'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="p-2 border-t border-zinc-800">
          <button
            type="button"
            onClick={handleLogout}
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-zinc-400 hover:bg-zinc-800/50 hover:text-white w-full transition-colors"
          >
            <LogOut className="w-4 h-4 shrink-0" />
            Выйти
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto pl-56">
        {children}
      </main>
    </div>
  );
}

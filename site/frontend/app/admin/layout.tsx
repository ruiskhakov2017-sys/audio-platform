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
  const { profile, isAuthenticated, logout } = useAuthStore();

  useEffect(() => {
    if (!MOCK_ADMIN_ALLOW_DEV && (!isAuthenticated || profile?.role !== 'admin')) {
      router.push('/login');
      return;
    }
  }, [isAuthenticated, profile?.role, router]);

  const handleLogout = () => {
    logout();
    router.push('/');
  };

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

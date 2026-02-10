'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Compass, Library } from 'lucide-react';
import { motion } from 'framer-motion';

const SIDEBAR_WIDTH = 300;

const navItems = [
  { href: '/', label: 'Главная', icon: Home },
  { href: '/browse', label: 'Поиск по категориям', icon: Compass },
  { href: '/library', label: 'Моя коллекция', icon: Library },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="fixed left-0 top-0 z-40 hidden h-screen flex-col border-r border-white/5 md:flex"
      style={{
        width: SIDEBAR_WIDTH,
        backgroundColor: 'rgba(0, 29, 61, 0.6)',
        backdropFilter: 'blur(40px)',
        WebkitBackdropFilter: 'blur(40px)',
      }}
    >
      <div className="flex flex-1 flex-col px-4 py-6">
        <div className="mb-8 flex items-center gap-3 px-2">
          <div
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[2.5rem] border border-white/5"
            style={{ backgroundColor: 'rgba(0, 180, 216, 0.15)' }}
          >
            <span className="text-sm font-bold tracking-tight text-[#00B4D8]">AP</span>
          </div>
          <span className="text-lg font-bold tracking-tight text-white">
            Аудио-портал
          </span>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {navItems.map((item) => {
            const isActive =
              item.href === '/'
                ? pathname === '/'
                : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link key={item.label} href={item.href}>
                <motion.div
                  className={`flex items-center gap-3 rounded-[2.5rem] px-3 py-2.5 text-sm font-medium transition-colors no-underline ${
                    isActive
                      ? 'text-[#00B4D8]'
                      : 'text-zinc-400 hover:bg-white/5 hover:text-[#00B4D8]/80'
                  }`}
                  style={{
                    boxShadow: isActive ? '0 0 20px rgba(0, 180, 216, 0.2)' : 'none',
                    border: isActive ? '1px solid rgba(0, 180, 216, 0.3)' : '1px solid transparent',
                  }}
                  whileHover={{ scale: 1.02 }}
                  whileTap={{ scale: 0.98 }}
                >
                  <Icon className="h-5 w-5 shrink-0" strokeWidth={1.5} />
                  {item.label}
                </motion.div>
              </Link>
            );
          })}
        </nav>
        <Link
          href="/"
          className="mt-4 rounded-[2.5rem] border border-white/5 px-3 py-2.5 text-center text-xs text-zinc-500 no-underline transition-colors hover:border-[#00B4D8]/30 hover:text-zinc-300"
        >
          ← Выбор версий
        </Link>
      </div>
    </aside>
  );
}

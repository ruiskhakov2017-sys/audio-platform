'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Home, Compass, Library } from 'lucide-react';

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
      className="fixed left-0 top-0 z-40 hidden h-screen flex-col border-r border-white/5 bg-[#001d3d]/60 backdrop-blur-[40px] md:flex"
      style={{ width: SIDEBAR_WIDTH }}
    >
      <div className="flex flex-1 flex-col px-4 py-6">
        <div className="mb-8 flex items-center gap-3 px-2">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[1rem] border border-white/10 bg-white/5 text-[#00B4D8]">
            <span className="text-sm font-black tracking-tight">AP</span>
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
              <Link
                key={item.label}
                href={item.href}
                className={`flex items-center gap-3 rounded-[1.25rem] px-3 py-2.5 text-sm font-medium transition-all duration-300 no-underline ${
                  isActive
                    ? 'bg-[#00B4D8]/15 text-[#00B4D8] border border-[#00B4D8]/30 shadow-[0_0_20px_rgba(0,180,216,0.15)]'
                    : 'text-white/60 hover:bg-white/5 hover:text-[#00B4D8] border border-transparent'
                }`}
              >
                <Icon className="h-5 w-5 shrink-0" strokeWidth={1.5} />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </aside>
  );
}

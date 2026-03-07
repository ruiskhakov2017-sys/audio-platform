'use client';

import { motion } from 'framer-motion';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Search, User, Heart, LogOut } from 'lucide-react';
import { useState, useEffect } from 'react';
import { useFavoritesStore } from '@/store/favoritesStore';
import { useAuthStore } from '@/store/authStore';
import { SearchModal } from '@/components/layout/SearchModal';

export function Header() {
    const router = useRouter();
    const [mounted, setMounted] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const [headerBg, setHeaderBg] = useState('rgba(0,8,20,0.8)');
    const likedIds = useFavoritesStore((s) => s.likedIds);
    const favoritesCount = likedIds.length;
    const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
    const profile = useAuthStore((s) => s.profile);
    const logout = useAuthStore((s) => s.logout);

    useEffect(() => {
        setMounted(true);
    }, []);

    useEffect(() => {
        if (typeof window === 'undefined') return;
        const onScroll = () => {
            setHeaderBg(window.scrollY > 100 ? 'rgba(0,8,20,0.8)' : 'rgba(0,8,20,0)');
        };
        onScroll();
        window.addEventListener('scroll', onScroll);
        return () => window.removeEventListener('scroll', onScroll);
    }, []);

    return (
        <header
            className="fixed top-0 left-0 right-0 z-50 border-b border-white/0 transition-[background-color] duration-200"
            style={{ backgroundColor: headerBg }}
        >
            <div className="backdrop-blur-xl min-h-24 flex items-center">
                <div className="max-w-[1800px] mx-auto px-6 py-4 w-full flex items-center justify-between">
                    {/* Logo */}
                    <Link href="/" className="font-heading">
                        <motion.div
                            className="text-3xl md:text-4xl font-black tracking-tight"
                            whileHover={{ scale: 1.05 }}
                        >
                            <span className="text-white">Dirty Secrets</span>
                        </motion.div>
                    </Link>

                    {/* Desktop Navigation: только на md и выше; на мобилке меню не рендерится */}
                    <nav className="hidden md:flex items-center flex-1 max-w-5xl mx-6 font-medium justify-center">
                        {/* Навигация */}
                        <div className="flex items-center gap-5">
                            <Link href="/catalog" className="nav-link text-[12px] uppercase tracking-widest text-zinc-400 hover:text-white transition-colors relative pb-0.5 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-white/80 hover:after:w-full after:transition-[width] after:duration-300">
                                Каталог
                            </Link>
                            <Link href="/top" className="nav-link text-[12px] uppercase tracking-widest text-zinc-400 hover:text-white transition-colors relative pb-0.5 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-white/80 hover:after:w-full after:transition-[width] after:duration-300">
                                Топ-100
                            </Link>
                        </div>
                        <div className="w-px h-4 bg-white/20 mx-2 xl:mx-4" aria-hidden />
                        {/* Услуги */}
                        <div className="flex items-center gap-5">
                            <Link href="/premium" className="nav-link inline-flex items-center gap-1.5 text-[12px] uppercase tracking-widest text-[#FFD700] border border-[#FFD700]/60 rounded px-2.5 py-1 hover:bg-[#FFD700]/10 hover:border-[#FFD700] hover:shadow-[0_0_10px_rgba(255,215,0,0.25)] transition-all relative pb-1 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-[#FFD700] hover:after:w-full after:transition-[width] after:duration-300">
                                <span aria-hidden>👑</span> Премиум
                            </Link>
                            <Link href="/order" className="nav-link text-[12px] uppercase tracking-widest text-zinc-400 hover:text-white transition-colors relative pb-0.5 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-white/80 hover:after:w-full after:transition-[width] after:duration-300">
                                Заказать историю
                            </Link>
                            <Link href="/reviews" className="nav-link text-[12px] uppercase tracking-widest text-zinc-400 hover:text-white transition-colors relative pb-0.5 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-white/80 hover:after:w-full after:transition-[width] after:duration-300">
                                Отзывы
                            </Link>
                        </div>
                        <div className="w-px h-4 bg-white/20 mx-2 xl:mx-4" aria-hidden />
                        {/* О нас */}
                        <div className="flex items-center">
                            <Link href="/about" className="nav-link text-[12px] uppercase tracking-widest text-zinc-400 hover:text-white transition-colors relative pb-0.5 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-white/80 hover:after:w-full after:transition-[width] after:duration-300">
                                О проекте
                            </Link>
                        </div>
                    </nav>

                    {/* Мобилка: только кнопка Поиск (без бургера и меню) */}
                    <div className="flex md:hidden items-center">
                        <button
                            type="button"
                            onClick={() => setSearchOpen(true)}
                            className="p-2.5 rounded-full hover:bg-white/5 transition-colors"
                            aria-label="Поиск"
                        >
                            <Search className="w-6 h-6 text-zinc-400" strokeWidth={1.5} />
                        </button>
                    </div>

                    {/* Right Icons (десктоп: md и выше) */}
                    <div className="hidden md:flex items-center gap-4">
                        <motion.button
                            type="button"
                            onClick={() => setSearchOpen(true)}
                            className="p-2.5 rounded-full hover:bg-white/5 transition-colors"
                            whileHover={{ scale: 1.1 }}
                            whileTap={{ scale: 0.9 }}
                            aria-label="Поиск"
                        >
                            <Search className="w-6 h-6 text-zinc-400" strokeWidth={1.5} />
                        </motion.button>
                        <Link
                            href="/favorites"
                            className="relative p-2.5 rounded-full hover:bg-white/5 transition-colors inline-flex"
                        >
                            <motion.div whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}>
                                <Heart className="w-6 h-6 text-zinc-400" strokeWidth={1.5} />
                            </motion.div>
                            {mounted && favoritesCount > 0 && (
                                <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 flex items-center justify-center rounded-full bg-rose-500 text-[10px] font-bold text-white">
                                    {favoritesCount > 99 ? '99+' : favoritesCount}
                                </span>
                            )}
                        </Link>
                        <Link
                            href={isAuthenticated ? '/profile' : '/login'}
                            className="p-0.5 rounded-full hover:bg-white/5 transition-colors inline-flex ring-0"
                        >
                            <motion.div whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }} className="rounded-full overflow-hidden w-9 h-9 flex items-center justify-center bg-[#00B4D8]/20 border border-[#00B4D8]/50">
                                {mounted && profile?.avatar_url ? (
                                    <Image src={profile.avatar_url} alt="" width={36} height={36} className="w-full h-full object-cover" unoptimized />
                                ) : (
                                    <User className="w-5 h-5 text-zinc-400" strokeWidth={1.5} />
                                )}
                            </motion.div>
                        </Link>
                        {mounted && isAuthenticated && (
                            <button
                                type="button"
                                onClick={async () => { await logout(); router.push('/'); }}
                                className="flex items-center gap-2 px-3 py-2 rounded-full text-sm text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
                                aria-label="Выйти"
                            >
                                <LogOut className="w-4 h-4" strokeWidth={1.5} />
                                <span className="hidden sm:inline">Выйти</span>
                            </button>
                        )}
                    </div>

                </div>
            </div>
            <SearchModal isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
        </header>
    );
}

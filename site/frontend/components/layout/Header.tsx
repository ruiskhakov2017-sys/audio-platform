'use client';

import { motion, useScroll, useTransform } from 'framer-motion';
import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Search, User, Heart, Menu, X, LogOut } from 'lucide-react';
import { useState } from 'react';
import { useFavoritesStore } from '@/store/favoritesStore';
import { useAuthStore } from '@/store/authStore';
import { SearchModal } from '@/components/layout/SearchModal';

export function Header() {
    const router = useRouter();
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
    const [searchOpen, setSearchOpen] = useState(false);
    const likedIds = useFavoritesStore((s) => s.likedIds);
    const favoritesCount = likedIds.length;
    const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
    const profile = useAuthStore((s) => s.profile);
    const logout = useAuthStore((s) => s.logout);
    const { scrollY } = useScroll();

    // Background blur appears on scroll
    const headerBg = useTransform(
        scrollY,
        [0, 100],
        ['rgba(0, 8, 20, 0)', 'rgba(0, 8, 20, 0.8)']
    );

    return (
        <motion.header
            className="fixed top-0 left-0 right-0 z-50 border-b border-white/0 transition-all"
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

                    {/* Desktop Navigation: 6 разделов, группировка Навигация | Услуги | О нас */}
                    <nav className="hidden lg:flex items-center flex-1 max-w-5xl mx-6 font-medium justify-center">
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
                            <Link href="/premium" className="nav-link text-[12px] uppercase tracking-widest text-amber-400/95 border border-amber-400/50 rounded px-2.5 py-1 hover:bg-amber-400/10 hover:border-amber-400/70 transition-all relative pb-1 after:absolute after:left-0 after:bottom-0 after:h-[2px] after:w-0 after:bg-amber-400 hover:after:w-full after:transition-[width] after:duration-300">
                                Премиум
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

                    {/* Right Icons */}
                    <div className="hidden lg:flex items-center gap-4">
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
                            {favoritesCount > 0 && (
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
                                {profile?.avatar_url ? (
                                    <Image src={profile.avatar_url} alt="" width={36} height={36} className="w-full h-full object-cover" unoptimized />
                                ) : (
                                    <User className="w-5 h-5 text-zinc-400" strokeWidth={1.5} />
                                )}
                            </motion.div>
                        </Link>
                        {isAuthenticated && (
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

                    {/* Mobile Menu Button */}
                    <button
                        className="lg:hidden p-2"
                        onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                    >
                        {mobileMenuOpen ? (
                            <X className="w-6 h-6 text-white" />
                        ) : (
                            <Menu className="w-6 h-6 text-white" />
                        )}
                    </button>
                </div>

                {/* Mobile Menu */}
                {mobileMenuOpen && (
                    <motion.div
                        className="lg:hidden glass-strong border-t border-white/10"
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -20 }}
                    >
                        <div className="px-6 py-6 flex flex-col gap-1 max-h-[70vh] overflow-y-auto">
                            <Link href="/catalog" className="text-xs uppercase tracking-widest text-zinc-400 hover:text-white py-3 border-b border-white/5 transition-colors" onClick={() => setMobileMenuOpen(false)}>Каталог</Link>
                            <Link href="/top" className="text-xs uppercase tracking-widest text-zinc-400 hover:text-white py-3 border-b border-white/5 transition-colors" onClick={() => setMobileMenuOpen(false)}>Топ-100</Link>
                            <Link href="/premium" className="text-xs uppercase tracking-widest text-amber-400/95 py-3 border-b border-white/5 font-medium" onClick={() => setMobileMenuOpen(false)}>Премиум</Link>
                            <Link href="/order" className="text-xs uppercase tracking-widest text-zinc-400 hover:text-white py-3 border-b border-white/5 transition-colors" onClick={() => setMobileMenuOpen(false)}>Заказать историю</Link>
                            <Link href="/reviews" className="text-xs uppercase tracking-widest text-zinc-400 hover:text-white py-3 border-b border-white/5 transition-colors" onClick={() => setMobileMenuOpen(false)}>Отзывы</Link>
                            <Link href="/about" className="text-xs uppercase tracking-widest text-zinc-400 hover:text-white py-3 border-b border-white/5 transition-colors" onClick={() => setMobileMenuOpen(false)}>О проекте</Link>
                            <div className="flex gap-4 pt-4 border-t border-white/10">
                                <button
                                    type="button"
                                    className="p-2"
                                    onClick={() => { setMobileMenuOpen(false); setSearchOpen(true); }}
                                    aria-label="Поиск"
                                >
                                    <Search className="w-5 h-5 text-zinc-400" />
                                </button>
                                <Link
                                    href="/favorites"
                                    className="relative p-2 inline-flex"
                                    onClick={() => setMobileMenuOpen(false)}
                                >
                                    <Heart className="w-5 h-5 text-zinc-400" />
                                    {favoritesCount > 0 && (
                                        <span className="absolute top-0 right-0 min-w-[16px] h-4 px-1 flex items-center justify-center rounded-full bg-rose-500 text-[10px] font-bold text-white">
                                            {favoritesCount > 99 ? '99+' : favoritesCount}
                                        </span>
                                    )}
                                </Link>
                                <Link
                                    href={isAuthenticated ? '/profile' : '/login'}
                                    className="p-2 rounded-full overflow-hidden w-9 h-9 flex items-center justify-center bg-[#00B4D8]/20 border border-[#00B4D8]/50"
                                    onClick={() => setMobileMenuOpen(false)}
                                >
                                    {profile?.avatar_url ? (
                                        <Image src={profile.avatar_url} alt="" width={36} height={36} className="w-full h-full object-cover" unoptimized />
                                    ) : (
                                        <User className="w-5 h-5 text-zinc-400" />
                                    )}
                                </Link>
                                {isAuthenticated && (
                                    <button
                                        type="button"
                                        className="flex items-center gap-2 p-2 text-zinc-400 hover:text-white"
                                        onClick={async () => { setMobileMenuOpen(false); await logout(); router.push('/'); }}
                                    >
                                        <LogOut className="w-5 h-5" />
                                        <span>Выйти</span>
                                    </button>
                                )}
                            </div>
                        </div>
                    </motion.div>
                )}
            </div>
            <SearchModal isOpen={searchOpen} onClose={() => setSearchOpen(false)} />
        </motion.header>
    );
}

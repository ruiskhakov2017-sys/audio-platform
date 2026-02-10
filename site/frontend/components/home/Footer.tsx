'use client';

import Link from 'next/link';

export function Footer() {
    return (
        <footer className="border-t border-white/5 py-12 px-6 bg-[#000814]/50">
            <div className="max-w-7xl mx-auto">
                <div className="flex flex-col md:flex-row justify-between items-center gap-6 text-sm">
                    <div className="font-bold text-white">
                        EroticAudio
                    </div>
                    <div className="flex flex-wrap justify-center gap-6 md:gap-8 text-zinc-500">
                        <Link href="#" className="hover:text-[#00B4D8] transition-colors">
                            Правила сервиса
                        </Link>
                        <Link href="#" className="hover:text-[#00B4D8] transition-colors">
                            Конфиденциальность
                        </Link>
                        <a href="https://t.me/example" target="_blank" rel="noopener noreferrer" className="hover:text-[#00B4D8] transition-colors">
                            Поддержка в Telegram
                        </a>
                    </div>
                </div>
                <p className="text-zinc-500 text-sm text-center mt-8">
                    © 2024 EroticAudio. Все права защищены. 18+
                </p>
            </div>
        </footer>
    );
}

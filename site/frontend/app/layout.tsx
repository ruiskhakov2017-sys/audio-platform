import type { Metadata } from "next";
import type { ReactNode } from 'react';
import { Philosopher, Montserrat } from "next/font/google";
import "./globals.css";
import { GlobalAudioPlayer } from '@/components/player/GlobalAudioPlayer';
import { GlobalPlayerBar } from '@/components/player/GlobalPlayerBar';
import { PaywallModal } from '@/components/ui/PaywallModal';
import { AuthToast } from '@/components/ui/AuthToast';
import { AuthInitializer } from '@/components/auth/AuthInitializer';

const philosopher = Philosopher({
  subsets: ['cyrillic', 'latin'],
  weight: ['400', '700'],
  variable: '--font-philosopher',
});

const montserrat = Montserrat({
  subsets: ['cyrillic', 'latin'],
  weight: ['400', '500', '600', '700'],
  variable: '--font-montserrat',
});

export const metadata: Metadata = {
  title: "EroticAudio — искусство звука для вашего наслаждения",
  description: "Аудио-портал эротического контента. Погружение, звук и воображение.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="ru">
      <body
        className={`${philosopher.variable} ${montserrat.variable} font-sans antialiased min-h-screen text-white pb-24`}
        style={{ backgroundColor: '#000814' }}
      >
        <AuthInitializer />
        {children}
        <GlobalAudioPlayer />
        <GlobalPlayerBar />
        <PaywallModal />
        <AuthToast />
      </body>
    </html>
  );
}
